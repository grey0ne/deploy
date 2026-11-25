package main

import (
	"bufio"
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"path"
	"strings"
	"sync"
	"time"
)

// Table is an immutable routing table built from the accepted config files; a reload builds a
// new one and swaps it atomically, so requests in flight keep the table they started with.
type Table struct {
	hosts        map[string]*siteHandler
	tunnelServer *TunnelServer
	tunnelClient *TunnelClient
}

type siteHandler struct {
	Site
	routes []routeHandler // longest path first
}

type routeHandler struct {
	Route
	proxy *httputil.ReverseProxy
}

func buildTable(files map[string]*ParsedFile, transports *Transports) *Table {
	table := &Table{hosts: map[string]*siteHandler{}}
	for _, file := range files {
		if file.TunnelServer != nil {
			table.tunnelServer = file.TunnelServer
		}
		if file.TunnelClient != nil {
			table.tunnelClient = file.TunnelClient
		}
		for _, site := range file.Sites {
			handler := &siteHandler{Site: site}
			for _, route := range site.Routes {
				handler.routes = append(handler.routes, routeHandler{
					Route: route,
					proxy: newReverseProxy(route, transports.Get(route.Timeout)),
				})
			}
			for _, domain := range site.Domains {
				table.hosts[domain] = handler
			}
		}
	}
	return table
}

func (s *siteHandler) match(requestPath string) *routeHandler {
	// Matched on the cleaned path like nginx locations; the upstream gets the original one.
	cleaned := path.Clean("/" + requestPath)
	for i := range s.routes {
		route := &s.routes[i]
		if route.Path == "/" || cleaned == route.Path || strings.HasPrefix(cleaned, route.Path+"/") {
			return route
		}
	}
	return nil
}

func (s *siteHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	route := s.match(r.URL.Path)
	if route == nil {
		http.NotFound(w, r)
		return
	}
	if r.ContentLength > s.MaxBodySize {
		http.Error(w, http.StatusText(http.StatusRequestEntityTooLarge), http.StatusRequestEntityTooLarge)
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, s.MaxBodySize)
	if s.Compress {
		serveCompressed(route.proxy, w, r)
		return
	}
	route.proxy.ServeHTTP(w, r)
}

func newReverseProxy(route Route, transport http.RoundTripper) *httputil.ReverseProxy {
	upstream := route.Upstream
	return &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.SetURL(upstream)
			// Backends check the requested host (Django ALLOWED_HOSTS, S3 signatures).
			pr.Out.Host = pr.In.Host
			// The X-Forwarded-* headers of the client are already removed: only the proxy sets them.
			ip := clientIP(pr.In)
			header := pr.Out.Header
			header.Set("X-Forwarded-For", ip)
			header.Set("X-Forwarded-Host", pr.In.Host)
			header.Set("X-Forwarded-Proto", "https")
			header.Set("X-Real-IP", ip)
			header.Set("X-Scheme", "https")
		},
		Transport:    transport,
		ErrorHandler: proxyError,
		ErrorLog:     slog.NewLogLogger(slog.Default().Handler(), slog.LevelWarn),
	}
}

func proxyError(w http.ResponseWriter, r *http.Request, err error) {
	var tooLarge *http.MaxBytesError
	var netErr net.Error
	status := http.StatusBadGateway
	switch {
	case errors.As(err, &tooLarge):
		status = http.StatusRequestEntityTooLarge
	case errors.Is(err, context.Canceled):
		// The client went away; nobody reads the answer.
		status = 499
	case errors.Is(err, context.DeadlineExceeded) || errors.As(err, &netErr) && netErr.Timeout():
		status = http.StatusGatewayTimeout
	}
	if status != 499 {
		slog.Warn("upstream error", "host", r.Host, "path", r.URL.Path, "status", status, "error", err)
	}
	http.Error(w, http.StatusText(status), status)
}

func clientIP(r *http.Request) string {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func requestHost(r *http.Request) string {
	host := r.Host
	if h, _, err := net.SplitHostPort(host); err == nil {
		host = h
	}
	return normalizeHost(host)
}

// Transports keeps one upstream transport per response timeout across reloads, so a reload
// does not drop the pooled upstream connections. Upstream names are resolved for every new
// connection: recreated containers with new addresses are picked up without a reload.
type Transports struct {
	mu        sync.Mutex
	byTimeout map[time.Duration]*http.Transport
}

func NewTransports() *Transports {
	return &Transports{byTimeout: map[time.Duration]*http.Transport{}}
}

func (t *Transports) Get(timeout time.Duration) *http.Transport {
	t.mu.Lock()
	defer t.mu.Unlock()
	if transport, ok := t.byTimeout[timeout]; ok {
		return transport
	}
	transport := &http.Transport{
		DialContext:           (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		MaxIdleConns:          512,
		MaxIdleConnsPerHost:   64,
		IdleConnTimeout:       90 * time.Second,
		ResponseHeaderTimeout: timeout,
		ExpectContinueTimeout: time.Second,
	}
	t.byTimeout[timeout] = transport
	return transport
}

// logRequests writes one access log line per request.
func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		recorder := &statusRecorder{ResponseWriter: w}
		next.ServeHTTP(recorder, r)
		status := recorder.status
		if status == 0 {
			status = http.StatusOK
		}
		slog.Info("request",
			"host", r.Host,
			"method", r.Method,
			"uri", r.URL.RequestURI(),
			"proto", r.Proto,
			"status", status,
			"bytes", recorder.bytes,
			"duration", time.Since(start).Round(time.Millisecond),
			"ip", clientIP(r),
		)
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
	bytes  int64
}

func (s *statusRecorder) WriteHeader(status int) {
	if s.status == 0 || s.status < 200 {
		s.status = status
	}
	s.ResponseWriter.WriteHeader(status)
}

func (s *statusRecorder) Write(data []byte) (int, error) {
	if s.status == 0 {
		s.status = http.StatusOK
	}
	n, err := s.ResponseWriter.Write(data)
	s.bytes += int64(n)
	return n, err
}

func (s *statusRecorder) Flush() {
	_ = http.NewResponseController(s.ResponseWriter).Flush()
}

// Hijack serves WebSocket upgrades.
func (s *statusRecorder) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if s.status == 0 {
		s.status = http.StatusSwitchingProtocols
	}
	return http.NewResponseController(s.ResponseWriter).Hijack()
}

func (s *statusRecorder) Unwrap() http.ResponseWriter {
	return s.ResponseWriter
}

func redirectToHTTPS(w http.ResponseWriter, r *http.Request) {
	target := fmt.Sprintf("https://%s%s", requestHost(r), r.URL.RequestURI())
	status := http.StatusMovedPermanently
	if r.Method != http.MethodGet && r.Method != http.MethodHead {
		// Keeps the method and the body.
		status = http.StatusPermanentRedirect
	}
	http.Redirect(w, r, target, status)
}
