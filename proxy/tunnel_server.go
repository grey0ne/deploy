package main

import (
	"bufio"
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/hashicorp/yamux"
)

// The tunnel: a dev proxy opens one TLS connection to https://<tunnel_server.domain>/connect,
// authenticates with its token and lists the public domains it serves (HTTP Upgrade with a
// JSON body). After 101 Switching Protocols the connection carries a yamux session: the
// production proxy opens a stream for every upstream connection and speaks plain HTTP/1.1 to
// the dev proxy, which routes the requests to its local sites.
const (
	tunnelProtocol     = "proxy-tunnel/1"
	tunnelConnectPath  = "/connect"
	maxTunnelHelloSize = 64 << 10
)

type tunnelHello struct {
	Domains []string `json:"domains"`
}

func yamuxConfig() *yamux.Config {
	config := yamux.DefaultConfig()
	config.EnableKeepAlive = true
	config.KeepAliveInterval = 30 * time.Second
	config.ConnectionWriteTimeout = 30 * time.Second
	config.LogOutput = nil
	config.Logger = slog.NewLogLogger(slog.Default().Handler(), slog.LevelWarn)
	return config
}

// TunnelRegistry knows the connected dev proxies of the production proxy.
type TunnelRegistry struct {
	mu      sync.Mutex
	domains map[string]*tunnel
	// Domains of disconnected tunnels: answered 502 (with a certificate) instead of failing
	// the TLS handshake, until another tunnel takes them or the process restarts.
	offline map[string]string
	// Called with the domains of a new tunnel (certificate warm-up).
	onConnect func(domains []string)
}

type tunnel struct {
	client    string
	domains   []string
	session   *yamux.Session
	transport *http.Transport
	handler   atomic.Pointer[http.Handler]
}

func NewTunnelRegistry() *TunnelRegistry {
	return &TunnelRegistry{domains: map[string]*tunnel{}, offline: map[string]string{}}
}

// known reports whether the domain belongs to a connected or recently connected tunnel.
func (r *TunnelRegistry) known(domain string) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	_, active := r.domains[domain]
	_, offline := r.offline[domain]
	return active || offline
}

// Domains of the connected tunnels.
func (r *TunnelRegistry) Domains() []string {
	r.mu.Lock()
	defer r.mu.Unlock()
	domains := make([]string, 0, len(r.domains))
	for domain := range r.domains {
		domains = append(domains, domain)
	}
	sort.Strings(domains)
	return domains
}

// ServeDomain proxies a request for a tunnel domain to its dev proxy.
func (r *TunnelRegistry) ServeDomain(w http.ResponseWriter, req *http.Request, domain string) {
	r.mu.Lock()
	t := r.domains[domain]
	r.mu.Unlock()
	if t != nil {
		if handler := t.handler.Load(); handler != nil {
			(*handler).ServeHTTP(w, req)
			return
		}
	}
	http.Error(w, "the dev proxy of this domain is not connected", http.StatusBadGateway)
}

// claim reserves the domains for a new tunnel. A domain of a tunnel of the same client is
// taken over: the client reconnected before the old session was noticed as dead.
func (r *TunnelRegistry) claim(t *tunnel) (replaced []*tunnel, err error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, domain := range t.domains {
		if existing := r.domains[domain]; existing != nil && existing.client != t.client {
			return nil, fmt.Errorf("domain %s is served by another dev proxy", domain)
		}
	}
	old := map[*tunnel]bool{}
	for domain, existing := range r.domains {
		if existing.client == t.client {
			old[existing] = true
			delete(r.domains, domain)
		}
	}
	for _, domain := range t.domains {
		r.domains[domain] = t
		delete(r.offline, domain)
	}
	for existing := range old {
		replaced = append(replaced, existing)
	}
	return replaced, nil
}

// release forgets a closed tunnel; its domains stay known as offline.
func (r *TunnelRegistry) release(t *tunnel) {
	r.mu.Lock()
	defer r.mu.Unlock()
	for _, domain := range t.domains {
		if r.domains[domain] == t {
			delete(r.domains, domain)
			r.offline[domain] = t.client
		}
	}
}

// ServeConnect accepts a dev proxy. isTaken reports domains of the production config, which
// always win over tunnels.
func (r *TunnelRegistry) ServeConnect(w http.ResponseWriter, req *http.Request, server *TunnelServer, isTaken func(string) bool) {
	if req.URL.Path != tunnelConnectPath {
		http.NotFound(w, req)
		return
	}
	if req.Method != http.MethodPost || !strings.EqualFold(req.Header.Get("Upgrade"), tunnelProtocol) {
		http.Error(w, "POST with Upgrade: "+tunnelProtocol+" expected", http.StatusBadRequest)
		return
	}
	access, ok := server.authenticate(req.Header.Get("Authorization"))
	if !ok {
		http.Error(w, "invalid token", http.StatusUnauthorized)
		return
	}
	var hello tunnelHello
	if err := json.NewDecoder(io.LimitReader(req.Body, maxTunnelHelloSize)).Decode(&hello); err != nil {
		http.Error(w, "invalid hello: "+err.Error(), http.StatusBadRequest)
		return
	}
	domains, status, err := checkTunnelDomains(hello.Domains, access, server, isTaken)
	if err != nil {
		slog.Warn("tunnel rejected", "client", access.Name, "error", err)
		http.Error(w, err.Error(), status)
		return
	}
	t := &tunnel{client: access.Name, domains: domains}
	replaced, err := r.claim(t)
	if err != nil {
		slog.Warn("tunnel rejected", "client", access.Name, "error", err)
		http.Error(w, err.Error(), http.StatusConflict)
		return
	}
	for _, old := range replaced {
		old.close()
	}
	conn, buffered, err := http.NewResponseController(w).Hijack()
	if err != nil {
		r.release(t)
		http.Error(w, "the connection cannot be upgraded: "+err.Error(), http.StatusInternalServerError)
		return
	}
	_ = conn.SetDeadline(time.Time{})
	fmt.Fprintf(buffered, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: %s\r\nConnection: Upgrade\r\n\r\n", tunnelProtocol)
	if err := buffered.Flush(); err != nil {
		conn.Close()
		r.release(t)
		return
	}
	session, err := yamux.Client(&bufferedConn{Conn: conn, reader: buffered.Reader}, yamuxConfig())
	if err != nil {
		conn.Close()
		r.release(t)
		return
	}
	t.attach(session)
	slog.Info("tunnel connected", "client", t.client, "domains", strings.Join(t.domains, " "), "ip", clientIP(req))
	if r.onConnect != nil {
		r.onConnect(t.domains)
	}
	go func() {
		<-session.CloseChan()
		r.release(t)
		t.close()
		slog.Info("tunnel disconnected", "client", t.client, "domains", strings.Join(t.domains, " "))
	}()
}

func checkTunnelDomains(requested []string, access TunnelAccess, server *TunnelServer, isTaken func(string) bool) ([]string, int, error) {
	if len(requested) == 0 {
		return nil, http.StatusBadRequest, errors.New("no domains")
	}
	seen := map[string]bool{}
	var domains []string
	for _, value := range requested {
		domain, err := validateDomain(value)
		if err != nil {
			return nil, http.StatusBadRequest, err
		}
		if seen[domain] {
			continue
		}
		seen[domain] = true
		if !access.allows(domain) {
			return nil, http.StatusForbidden, fmt.Errorf("domain %s is not allowed for %s", domain, access.Name)
		}
		if domain == server.Domain || isTaken(domain) {
			return nil, http.StatusConflict, fmt.Errorf("domain %s is served by the production config", domain)
		}
		domains = append(domains, domain)
	}
	sort.Strings(domains)
	return domains, 0, nil
}

// authenticate compares the bearer token with every client in constant time.
func (s *TunnelServer) authenticate(header string) (TunnelAccess, bool) {
	token, ok := strings.CutPrefix(header, "Bearer ")
	if !ok {
		return TunnelAccess{}, false
	}
	var found TunnelAccess
	matched := false
	for _, access := range s.Clients {
		if subtle.ConstantTimeCompare([]byte(token), []byte(access.Token)) == 1 {
			found, matched = access, true
		}
	}
	return found, matched
}

// attach starts proxying over the session: every upstream connection is a yamux stream.
func (t *tunnel) attach(session *yamux.Session) {
	t.session = session
	t.transport = &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			return session.Open()
		},
		MaxIdleConnsPerHost:   64,
		IdleConnTimeout:       90 * time.Second,
		ResponseHeaderTimeout: 5 * time.Minute,
		ExpectContinueTimeout: time.Second,
	}
	// The dev proxy routes by Host, which newReverseProxy keeps; the URL host is a placeholder.
	var handler http.Handler = newReverseProxy(Route{Upstream: &url.URL{Scheme: "http", Host: "tunnel"}}, t.transport)
	t.handler.Store(&handler)
}

func (t *tunnel) close() {
	if t.session != nil {
		_ = t.session.Close()
	}
	if t.transport != nil {
		t.transport.CloseIdleConnections()
	}
}

// bufferedConn reads what the HTTP reader has already buffered before the connection itself.
type bufferedConn struct {
	net.Conn
	reader *bufio.Reader
}

func (c *bufferedConn) Read(p []byte) (int, error) {
	return c.reader.Read(p)
}
