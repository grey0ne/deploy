package main

import (
	"bufio"
	"compress/gzip"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func writeFile(t *testing.T, path, content string) {
	t.Helper()
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

// writeCert writes a self-signed certificate for the domain; serial tells the versions apart.
func writeCert(t *testing.T, dir, name, domain string, serial int64) (string, string) {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := &x509.Certificate{
		SerialNumber: big.NewInt(serial),
		Subject:      pkix.Name{CommonName: domain},
		DNSNames:     []string{domain},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
	}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		t.Fatal(err)
	}
	certPath := filepath.Join(dir, name+".crt")
	keyPath := filepath.Join(dir, name+".key")
	writeFile(t, certPath, string(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der})))
	writeFile(t, keyPath, string(pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})))
	return certPath, keyPath
}

func siteYAML(domain, upstream string) string {
	return fmt.Sprintf(`
sites:
  - domains: [%s]
    tls: {acme: true}
    routes:
      - paths: [/]
        upstream: %s
`, domain, upstream)
}

func TestParseFile(t *testing.T) {
	sites, err := ParseFile([]byte(`
sites:
  - domains: [Example.COM., www.example.com]
    tls: {cert: /c.crt, key: /c.key}
    max_body_size: 50000k
    compress: false
    routes:
      - paths: [/api, /admin]
        upstream: http://django:8000
        timeout: 120s
      - paths: [/]
        upstream: http://nextjs:3000/
`))
	if err != nil {
		t.Fatal(err)
	}
	site := sites[0]
	if site.Domains[0] != "example.com" || site.MaxBodySize != 50000*1024 || site.Compress {
		t.Fatalf("unexpected site %+v", site)
	}
	if got := site.Routes[len(site.Routes)-1]; got.Path != "/" || got.Upstream.String() != "http://nextjs:3000" || got.Timeout != defaultTimeout {
		t.Fatalf("catch-all route must be the last one: %+v", got)
	}
	if site.Routes[0].Timeout != 120*time.Second {
		t.Fatalf("timeout: %v", site.Routes[0].Timeout)
	}

	empty, err := ParseFile(nil)
	if err != nil || len(empty) != 0 {
		t.Fatalf("an empty file has no sites: %v %v", empty, err)
	}
}

func TestParseFileErrors(t *testing.T) {
	route := "routes: [{paths: [/], upstream: 'http://a:1'}]"
	cases := map[string]string{
		"unknown field":    "sites: [{domains: [a.com], tls: {acme: true}, " + route + ", proxy_pass: x}]",
		"no domains":       "sites: [{tls: {acme: true}, " + route + "}]",
		"bad domain":       "sites: [{domains: ['a b.com'], tls: {acme: true}, " + route + "}]",
		"acme and files":   "sites: [{domains: [a.com], tls: {acme: true, cert: /c}, " + route + "}]",
		"no tls":           "sites: [{domains: [a.com], " + route + "}]",
		"key missing":      "sites: [{domains: [a.com], tls: {cert: /c}, " + route + "}]",
		"bad size":         "sites: [{domains: [a.com], tls: {acme: true}, max_body_size: 10mb, " + route + "}]",
		"no routes":        "sites: [{domains: [a.com], tls: {acme: true}}]",
		"relative path":    "sites: [{domains: [a.com], tls: {acme: true}, routes: [{paths: [api], upstream: 'http://a:1'}]}]",
		"trailing slash":   "sites: [{domains: [a.com], tls: {acme: true}, routes: [{paths: [/api/], upstream: 'http://a:1'}]}]",
		"upstream path":    "sites: [{domains: [a.com], tls: {acme: true}, routes: [{paths: [/], upstream: 'http://a:1/x'}]}]",
		"upstream scheme":  "sites: [{domains: [a.com], tls: {acme: true}, routes: [{paths: [/], upstream: 'a:1'}]}]",
		"bad timeout":      "sites: [{domains: [a.com], tls: {acme: true}, routes: [{paths: [/], upstream: 'http://a:1', timeout: 10}]}]",
		"duplicate path":   "sites: [{domains: [a.com], tls: {acme: true}, routes: [{paths: [/], upstream: 'http://a:1'}, {paths: [/], upstream: 'http://b:1'}]}]",
		"duplicate domain": "sites: [{domains: [a.com], tls: {acme: true}, " + route + "}, {domains: [A.com], tls: {acme: true}, " + route + "}]",
	}
	for name, content := range cases {
		if _, err := ParseFile([]byte(content)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}
}

func TestParseSize(t *testing.T) {
	for value, want := range map[string]int64{"1048576": 1 << 20, "100m": 100 << 20, "50000k": 50000 << 10, "1G": 1 << 30} {
		if got, err := parseSize(value); err != nil || got != want {
			t.Errorf("%s: got %d, %v", value, got, err)
		}
	}
	for _, value := range []string{"", "0", "-1", "1 m", "1mb", "99999999999999999g"} {
		if _, err := parseSize(value); err == nil {
			t.Errorf("%s: expected an error", value)
		}
	}
}

func TestMatch(t *testing.T) {
	sites, err := ParseFile([]byte(`
sites:
  - domains: [a.com]
    tls: {acme: true}
    routes:
      - {paths: [/api, /openapi.json], upstream: 'http://api:1'}
      - {paths: [/api/ws], upstream: 'http://ws:1'}
      - {paths: [/], upstream: 'http://web:1'}
`))
	if err != nil {
		t.Fatal(err)
	}
	site := buildTable(map[string][]Site{"a.yaml": sites}, NewTransports()).hosts["a.com"]
	cases := map[string]string{
		"/api":            "api",
		"/api/":           "api",
		"/api/x":          "api",
		"/apix":           "web",
		"/api/ws/1":       "ws",
		"/openapi.json":   "api",
		"/":               "web",
		"":                "web",
		"/x/../api/users": "api",
		"//api//users":    "api",
		"/static/../../":  "web",
	}
	for requestPath, want := range cases {
		if got := site.match(requestPath).Upstream.Hostname(); got != want {
			t.Errorf("%q: got %s, want %s", requestPath, got, want)
		}
	}
	noCatchAll := &siteHandler{routes: site.routes[:2]}
	if noCatchAll.match("/other") != nil {
		t.Error("a path outside the routes must not match")
	}
}

func TestLoadDirIsolatesFiles(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "a.yaml"), siteYAML("a.com", "http://a:1"))
	writeFile(t, filepath.Join(dir, "b.yaml"), siteYAML("b.com", "http://b:1"))
	writeFile(t, filepath.Join(dir, ".c.yaml"), "broken: [")
	writeFile(t, filepath.Join(dir, "notes.txt"), "broken: [")
	certs := NewCertFiles()

	accepted, errs, err := loadDir(dir, nil, certs)
	if err != nil || len(errs) != 0 || len(accepted) != 2 {
		t.Fatalf("accepted %v, errs %v, err %v", accepted, errs, err)
	}

	// A broken file keeps its previous version, the other files are not affected.
	writeFile(t, filepath.Join(dir, "b.yaml"), "sites: [")
	next, errs, _ := loadDir(dir, accepted, certs)
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "previous version") {
		t.Fatalf("errs %v", errs)
	}
	if next["b.yaml"][0].Domains[0] != "b.com" || next["a.yaml"] == nil {
		t.Fatalf("accepted %v", next)
	}

	// Without a previous version it is not served.
	fresh, errs, _ := loadDir(dir, nil, certs)
	if len(errs) != 1 || fresh["b.yaml"] != nil {
		t.Fatalf("accepted %v, errs %v", fresh, errs)
	}

	// A domain of an earlier file is not taken over.
	writeFile(t, filepath.Join(dir, "b.yaml"), siteYAML("a.com", "http://b:1"))
	next, errs, _ = loadDir(dir, accepted, certs)
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "a.com (in a.yaml)") {
		t.Fatalf("errs %v", errs)
	}
	if next["a.yaml"][0].Routes[0].Upstream.Host != "a:1" || next["b.yaml"][0].Domains[0] != "b.com" {
		t.Fatalf("accepted %v", next)
	}

	if _, _, err := loadDir(filepath.Join(dir, "missing"), nil, certs); err == nil {
		t.Fatal("a missing directory is an error")
	}
}

func TestLoadDirChecksCertificates(t *testing.T) {
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "a.yaml"), `
sites:
  - domains: [a.com]
    tls: {cert: /missing.crt, key: /missing.key}
    routes: [{paths: [/], upstream: 'http://a:1'}]
`)
	_, errs, _ := loadDir(dir, nil, NewCertFiles())
	if len(errs) != 1 || !strings.Contains(errs[0].Error(), "certificate") {
		t.Fatalf("errs %v", errs)
	}
}

// echo answers with the request it received as JSON.
func echoUpstream(t *testing.T) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/big" {
			w.Header().Set("Content-Type", "application/json")
			w.Header().Set("ETag", `"v1"`)
			fmt.Fprintf(w, `{"data": "%s"}`, strings.Repeat("x", 5000))
			return
		}
		if r.URL.Path == "/upload" {
			if _, err := io.ReadAll(r.Body); err != nil {
				return
			}
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(map[string]string{
			"host":   r.Host,
			"uri":    r.URL.RequestURI(),
			"xff":    r.Header.Get("X-Forwarded-For"),
			"proto":  r.Header.Get("X-Forwarded-Proto"),
			"realip": r.Header.Get("X-Real-IP"),
			"xhost":  r.Header.Get("X-Forwarded-Host"),
		})
	}))
	t.Cleanup(server.Close)
	return server
}

func newTestProxy(t *testing.T, config string) *Proxy {
	t.Helper()
	dir := t.TempDir()
	writeFile(t, filepath.Join(dir, "site.yaml"), config)
	proxy := NewProxy(Options{ConfigDir: dir, PollInterval: time.Hour})
	proxy.Reload()
	return proxy
}

func TestProxyRequest(t *testing.T) {
	upstream := echoUpstream(t)
	proxy := newTestProxy(t, fmt.Sprintf(`
sites:
  - domains: [a.com]
    tls: {acme: true}
    max_body_size: 10
    routes: [{paths: [/], upstream: '%s'}]
`, upstream.URL))
	handler := proxy.HTTPSHandler()

	request := httptest.NewRequest("GET", "https://A.com:443/path?q=1", nil)
	request.RemoteAddr = "203.0.113.5:1234"
	request.Header.Set("X-Forwarded-For", "1.2.3.4")
	request.Header.Set("X-Real-IP", "1.2.3.4")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Code != 200 {
		t.Fatalf("status %d: %s", response.Code, response.Body)
	}
	var got map[string]string
	if err := json.Unmarshal(response.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	want := map[string]string{
		"host": "A.com:443", "uri": "/path?q=1", "xff": "203.0.113.5", "proto": "https",
		"realip": "203.0.113.5", "xhost": "A.com:443",
	}
	for key, value := range want {
		if got[key] != value {
			t.Errorf("%s: got %q, want %q", key, got[key], value)
		}
	}

	tooLarge := httptest.NewRequest("POST", "https://a.com/upload", strings.NewReader(strings.Repeat("x", 11)))
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, tooLarge)
	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("declared length over the limit: status %d", response.Code)
	}

	// Chunked: the length is known only while reading.
	chunked := httptest.NewRequest("POST", "https://a.com/upload", io.MultiReader(strings.NewReader(strings.Repeat("x", 11))))
	chunked.ContentLength = -1
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, chunked)
	if response.Code != http.StatusRequestEntityTooLarge {
		t.Fatalf("streamed body over the limit: status %d", response.Code)
	}

	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "https://other.com/", nil))
	if response.Code != http.StatusMisdirectedRequest {
		t.Fatalf("unknown host: status %d", response.Code)
	}
}

func TestProxyUpstreamDown(t *testing.T) {
	proxy := newTestProxy(t, siteYAML("a.com", "http://127.0.0.1:1"))
	response := httptest.NewRecorder()
	proxy.HTTPSHandler().ServeHTTP(response, httptest.NewRequest("GET", "https://a.com/", nil))
	if response.Code != http.StatusBadGateway {
		t.Fatalf("status %d", response.Code)
	}
}

func TestCompression(t *testing.T) {
	upstream := echoUpstream(t)
	proxy := newTestProxy(t, siteYAML("a.com", upstream.URL))
	handler := proxy.HTTPSHandler()

	request := httptest.NewRequest("GET", "https://a.com/big", nil)
	request.Header.Set("Accept-Encoding", "br, gzip")
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	if response.Header().Get("Content-Encoding") != "gzip" || response.Header().Get("ETag") != `W/"v1"` {
		t.Fatalf("headers %v", response.Header())
	}
	reader, err := gzip.NewReader(response.Body)
	if err != nil {
		t.Fatal(err)
	}
	body, err := io.ReadAll(reader)
	if err != nil || len(body) != 5000+len(`{"data": ""}`) {
		t.Fatalf("body of %d bytes, %v", len(body), err)
	}

	small := httptest.NewRequest("GET", "https://a.com/small", nil)
	small.Header.Set("Accept-Encoding", "gzip")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, small)
	if response.Header().Get("Content-Encoding") != "" {
		t.Fatal("a short response must not be compressed")
	}

	refused := httptest.NewRequest("GET", "https://a.com/big", nil)
	refused.Header.Set("Accept-Encoding", "gzip;q=0")
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, refused)
	if response.Header().Get("Content-Encoding") != "" {
		t.Fatal("gzip;q=0 refuses gzip")
	}
}

func TestHTTPRedirect(t *testing.T) {
	proxy := newTestProxy(t, siteYAML("a.com", "http://a:1"))
	handler := proxy.HTTPHandler()

	response := httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "http://a.com/x?y=1", nil))
	if response.Code != http.StatusMovedPermanently || response.Header().Get("Location") != "https://a.com/x?y=1" {
		t.Fatalf("status %d, location %s", response.Code, response.Header().Get("Location"))
	}
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("POST", "http://a.com/x", nil))
	if response.Code != http.StatusPermanentRedirect {
		t.Fatalf("POST: status %d", response.Code)
	}
	response = httptest.NewRecorder()
	handler.ServeHTTP(response, httptest.NewRequest("GET", "http://b.com/", nil))
	if response.Code != http.StatusNotFound {
		t.Fatalf("unknown host: status %d", response.Code)
	}
}

func TestReloadAndCertificateRefresh(t *testing.T) {
	dir := t.TempDir()
	certDir := t.TempDir()
	certPath, keyPath := writeCert(t, certDir, "a", "a.com", 1)
	writeFile(t, filepath.Join(dir, "a.yaml"), fmt.Sprintf(`
sites:
  - domains: [a.com]
    tls: {cert: %s, key: %s}
    routes: [{paths: [/], upstream: 'http://a:1'}]
`, certPath, keyPath))
	proxy := NewProxy(Options{ConfigDir: dir, PollInterval: time.Hour})
	proxy.Reload()

	serial := func() int64 {
		t.Helper()
		cert, err := proxy.GetCertificate(&tls.ClientHelloInfo{ServerName: "a.com"})
		if err != nil {
			t.Fatal(err)
		}
		parsed, err := x509.ParseCertificate(cert.Certificate[0])
		if err != nil {
			t.Fatal(err)
		}
		return parsed.SerialNumber.Int64()
	}
	if serial() != 1 {
		t.Fatal("first certificate expected")
	}
	if _, err := proxy.GetCertificate(&tls.ClientHelloInfo{ServerName: "b.com"}); err == nil {
		t.Fatal("unknown server name must fail the handshake")
	}

	// A renewed certificate is served without a reload of the config.
	writeCert(t, certDir, "a", "a.com", 2)
	future := time.Now().Add(time.Minute)
	for _, path := range []string{certPath, keyPath} {
		if err := os.Chtimes(path, future, future); err != nil {
			t.Fatal(err)
		}
	}
	proxy.certs.Refresh(usedCerts(proxy.table.Load()))
	if serial() != 2 {
		t.Fatal("renewed certificate expected")
	}

	// A new file is picked up by the directory state check.
	before := proxy.dirState
	writeFile(t, filepath.Join(dir, "b.yaml"), siteYAML("b.com", "http://b:1"))
	if dirState(dir) == before {
		t.Fatal("a new file must change the directory state")
	}
	proxy.Reload()
	if proxy.site("b.com") == nil {
		t.Fatal("b.com must be served after the reload")
	}
	if err := proxy.acmeHostPolicy(context.Background(), "b.com"); err != nil {
		t.Fatal(err)
	}
	if err := proxy.acmeHostPolicy(context.Background(), "a.com"); err == nil {
		t.Fatal("a.com uses certificate files, not ACME")
	}
}

func TestWebSocketUpgrade(t *testing.T) {
	// The upstream switches protocols and echoes what it reads.
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Upgrade") != "websocket" || r.Host != "a.com" {
			http.Error(w, "upgrade expected", http.StatusBadRequest)
			return
		}
		conn, buffered, err := http.NewResponseController(w).Hijack()
		if err != nil {
			return
		}
		defer conn.Close()
		fmt.Fprint(buffered, "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n")
		_ = buffered.Flush()
		line, _ := buffered.ReadString('\n')
		fmt.Fprint(buffered, "echo "+line)
		_ = buffered.Flush()
	}))
	t.Cleanup(upstream.Close)
	proxy := newTestProxy(t, siteYAML("a.com", upstream.URL))
	server := httptest.NewServer(proxy.HTTPSHandler())
	t.Cleanup(server.Close)

	conn, err := net.Dial("tcp", server.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	fmt.Fprint(conn, "GET /ws HTTP/1.1\r\nHost: a.com\r\nConnection: Upgrade\r\nUpgrade: websocket\r\nAccept-Encoding: gzip\r\n\r\n")
	reader := bufio.NewReader(conn)
	response, err := http.ReadResponse(reader, nil)
	if err != nil {
		t.Fatal(err)
	}
	if response.StatusCode != http.StatusSwitchingProtocols {
		t.Fatalf("status %d", response.StatusCode)
	}
	fmt.Fprint(conn, "ping\n")
	line, err := reader.ReadString('\n')
	if err != nil || line != "echo ping\n" {
		t.Fatalf("got %q, %v", line, err)
	}
}
