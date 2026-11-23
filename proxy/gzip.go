package main

import (
	"bufio"
	"compress/gzip"
	"errors"
	"mime"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
)

// Responses shorter than this are not worth compressing.
const compressMinLength = 1000

var compressibleTypes = map[string]bool{
	"application/javascript":    true,
	"application/json":          true,
	"application/manifest+json": true,
	"application/xml":           true,
	"application/x-javascript":  true,
	"image/svg+xml":             true,
	"text/css":                  true,
	"text/html":                 true,
	"text/javascript":           true,
	"text/plain":                true,
	"text/xml":                  true,
}

var gzipWriters = sync.Pool{New: func() any {
	writer, _ := gzip.NewWriterLevel(nil, gzip.BestSpeed)
	return writer
}}

// serveCompressed gzips the text responses the upstream left uncompressed, when the client
// accepts gzip. Streams (server-sent events), upgrades and encoded responses pass through.
func serveCompressed(next http.Handler, w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodHead || r.Header.Get("Upgrade") != "" || !acceptsGzip(r) {
		next.ServeHTTP(w, r)
		return
	}
	writer := &gzipResponseWriter{ResponseWriter: w}
	defer writer.close()
	next.ServeHTTP(writer, r)
}

func acceptsGzip(r *http.Request) bool {
	for _, part := range strings.Split(r.Header.Get("Accept-Encoding"), ",") {
		coding, params, _ := strings.Cut(strings.TrimSpace(part), ";")
		if !strings.EqualFold(strings.TrimSpace(coding), "gzip") {
			continue
		}
		for _, param := range strings.Split(params, ";") {
			name, value, _ := strings.Cut(strings.TrimSpace(param), "=")
			if name == "q" {
				q, err := strconv.ParseFloat(value, 64)
				return err == nil && q > 0
			}
		}
		return true
	}
	return false
}

type gzipResponseWriter struct {
	http.ResponseWriter
	decided bool
	gz      *gzip.Writer
}

func (g *gzipResponseWriter) WriteHeader(status int) {
	if status < 200 && status != http.StatusSwitchingProtocols {
		// 1xx informational responses (103 Early Hints) are followed by the real one.
		g.ResponseWriter.WriteHeader(status)
		return
	}
	if !g.decided {
		g.decided = true
		if g.shouldCompress(status) {
			header := g.Header()
			header.Del("Content-Length")
			header.Set("Content-Encoding", "gzip")
			header.Add("Vary", "Accept-Encoding")
			// The compressed body is not byte-identical: a strong validator must become weak.
			if etag := header.Get("ETag"); etag != "" && !strings.HasPrefix(etag, "W/") {
				header.Set("ETag", "W/"+etag)
			}
			g.gz = gzipWriters.Get().(*gzip.Writer)
			g.gz.Reset(g.ResponseWriter)
		}
	}
	g.ResponseWriter.WriteHeader(status)
}

func (g *gzipResponseWriter) shouldCompress(status int) bool {
	if status < 200 || status == http.StatusNoContent || status == http.StatusNotModified || status == http.StatusPartialContent {
		return false
	}
	header := g.Header()
	if header.Get("Content-Encoding") != "" || header.Get("Content-Range") != "" {
		return false
	}
	mediaType, _, err := mime.ParseMediaType(header.Get("Content-Type"))
	if err != nil || !compressibleTypes[mediaType] {
		return false
	}
	if length := header.Get("Content-Length"); length != "" {
		if n, err := strconv.Atoi(length); err == nil && n < compressMinLength {
			return false
		}
	}
	return true
}

func (g *gzipResponseWriter) Write(data []byte) (int, error) {
	if !g.decided {
		g.WriteHeader(http.StatusOK)
	}
	if g.gz != nil {
		return g.gz.Write(data)
	}
	return g.ResponseWriter.Write(data)
}

func (g *gzipResponseWriter) Flush() {
	if g.gz != nil {
		_ = g.gz.Flush()
	}
	_ = http.NewResponseController(g.ResponseWriter).Flush()
}

func (g *gzipResponseWriter) Hijack() (net.Conn, *bufio.ReadWriter, error) {
	if g.gz != nil {
		return nil, nil, errors.New("hijacking a compressed response")
	}
	return http.NewResponseController(g.ResponseWriter).Hijack()
}

func (g *gzipResponseWriter) Unwrap() http.ResponseWriter {
	return g.ResponseWriter
}

func (g *gzipResponseWriter) close() {
	if g.gz == nil {
		return
	}
	_ = g.gz.Close()
	g.gz.Reset(nil)
	gzipWriters.Put(g.gz)
	g.gz = nil
}
