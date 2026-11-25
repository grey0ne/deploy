package main

import (
	"bufio"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const testToken = "0123456789abcdef0123456789abcdef"

func tunnelServerYAML(certPath, keyPath string) string {
	return fmt.Sprintf(`
tunnel_server:
  domain: tunnel.test
  tls: {cert: %s, key: %s}
  clients:
    - name: laptop
      token: %s
      domains: ["*.dev.test", exact.test]
`, certPath, keyPath, testToken)
}

func TestTunnelConfigErrors(t *testing.T) {
	server := func(clients string) string {
		return "tunnel_server: {domain: tunnel.test, tls: {acme: true}, clients: [" + clients + "]}"
	}
	client := func(fields string) string {
		return "tunnel_client: {server: tunnel.test, token: " + testToken + ", " + fields + "}"
	}
	cases := map[string]string{
		"short token":       server("{name: a, token: short, domains: [a.test]}"),
		"no domains":        server("{name: a, token: " + testToken + "}"),
		"bare wildcard":     server("{name: a, token: " + testToken + ", domains: ['*.test']}"),
		"no clients":        server(""),
		"no tls":            "tunnel_server: {domain: tunnel.test, clients: [{name: a, token: " + testToken + ", domains: [a.test]}]}",
		"duplicate token":   server("{name: a, token: " + testToken + ", domains: [a.test]}, {name: b, token: " + testToken + ", domains: [b.test]}"),
		"site domain":       server("{name: a, token: "+testToken+", domains: [a.test]}") + "\nsites: [{domains: [tunnel.test], tls: {acme: true}, routes: [{paths: [/], upstream: 'http://a:1'}]}]",
		"no expose":         client("expose: []"),
		"duplicate expose":  client("expose: [{domain: a.dev.test, site: a.local}, {domain: a.dev.test, site: b.local}]"),
		"bad expose domain": client("expose: [{domain: 'a b', site: a.local}]"),
		"bad server":        "tunnel_client: {server: 'a b:1', token: " + testToken + ", expose: [{domain: a.dev.test, site: a.local}]}",
	}
	for name, content := range cases {
		if _, err := ParseFile([]byte(content)); err == nil {
			t.Errorf("%s: expected an error", name)
		}
	}

	parsed, err := ParseFile([]byte(client("expose: [{domain: A.Dev.Test, site: a.local}]")))
	if err != nil {
		t.Fatal(err)
	}
	if c := parsed.TunnelClient; c.Address != "tunnel.test:443" || c.ServerName != "tunnel.test" || c.Expose["a.dev.test"] != "a.local" {
		t.Fatalf("unexpected client %+v", c)
	}
}

func TestTunnelAccessPatterns(t *testing.T) {
	access, err := validateTunnelAccess(TunnelAccessConfig{Name: "a", Token: testToken, Domains: []string{"*.dev.test", "exact.test"}})
	if err != nil {
		t.Fatal(err)
	}
	for domain, want := range map[string]bool{
		"a.dev.test": true, "a.b.dev.test": true, "dev.test": false, "adev.test": false,
		"exact.test": true, "a.exact.test": false, "other.test": false,
	} {
		if access.allows(domain) != want {
			t.Errorf("%s: allows=%v", domain, !want)
		}
	}
}

func TestOneTunnelSectionPerDirectory(t *testing.T) {
	dir := t.TempDir()
	client := "tunnel_client: {server: tunnel.test, token: " + testToken + ", expose: [{domain: a.dev.test, site: a.local}]}\n"
	writeFile(t, filepath.Join(dir, "a.yaml"), client)
	writeFile(t, filepath.Join(dir, "b.yaml"), client)
	accepted, errs, _ := loadDir(dir, nil, NewCertFiles())
	if accepted["a.yaml"] == nil || accepted["b.yaml"] != nil || len(errs) != 1 || !strings.Contains(errs[0].Error(), "already configured in a.yaml") {
		t.Fatalf("accepted %v, errs %v", accepted, errs)
	}
}

// newTunnelServerProxy: a production proxy with the tunnel endpoint and the site taken.dev.test.
func newTunnelServerProxy(t *testing.T) (*Proxy, *httptest.Server) {
	t.Helper()
	dir, certDir := t.TempDir(), t.TempDir()
	certPath, keyPath := writeCert(t, certDir, "tunnel", "tunnel.test", 1)
	siteCert, siteKey := writeCert(t, certDir, "taken", "taken.dev.test", 2)
	writeFile(t, filepath.Join(dir, "tunnel.yaml"), tunnelServerYAML(certPath, keyPath))
	writeFile(t, filepath.Join(dir, "site.yaml"), fmt.Sprintf(`
sites:
  - domains: [taken.dev.test]
    tls: {cert: %s, key: %s}
    routes: [{paths: [/], upstream: 'http://127.0.0.1:1'}]
`, siteCert, siteKey))
	proxy := NewProxy(Options{ConfigDir: dir, PollInterval: time.Hour})
	proxy.Reload()
	server := httptest.NewUnstartedServer(proxy.HTTPSHandler())
	server.TLS = proxy.TLSConfig()
	server.StartTLS()
	t.Cleanup(server.Close)
	return proxy, server
}

func connectRequest(token string, domains ...string) *http.Request {
	body, _ := json.Marshal(tunnelHello{Domains: domains})
	request := httptest.NewRequest("POST", "https://tunnel.test/connect", strings.NewReader(string(body)))
	request.Header.Set("Authorization", "Bearer "+token)
	request.Header.Set("Upgrade", tunnelProtocol)
	return request
}

func TestTunnelConnectRejections(t *testing.T) {
	proxy, _ := newTunnelServerProxy(t)
	handler := proxy.HTTPSHandler()
	cases := map[string]struct {
		request *http.Request
		status  int
	}{
		"bad token":         {connectRequest(strings.Repeat("x", 32), "a.dev.test"), http.StatusUnauthorized},
		"not allowed":       {connectRequest(testToken, "a.other.test"), http.StatusForbidden},
		"production site":   {connectRequest(testToken, "taken.dev.test"), http.StatusConflict},
		"tunnel domain":     {connectRequest(testToken, "tunnel.test"), http.StatusForbidden},
		"no domains":        {connectRequest(testToken), http.StatusBadRequest},
		"no upgrade header": {httptest.NewRequest("POST", "https://tunnel.test/connect", nil), http.StatusBadRequest},
		"other path":        {httptest.NewRequest("GET", "https://tunnel.test/", nil), http.StatusNotFound},
	}
	for name, c := range cases {
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, c.request)
		if response.Code != c.status {
			t.Errorf("%s: status %d, want %d: %s", name, response.Code, c.status, response.Body)
		}
	}
	if len(proxy.tunnels.Domains()) != 0 {
		t.Fatal("rejected tunnels must not register domains")
	}
}

func TestTunnelEndToEnd(t *testing.T) {
	prod, prodServer := newTunnelServerProxy(t)

	// The local service: the echo upstream and a WebSocket-like upgrade on /ws.
	upstream := echoUpstream(t)
	wsUpstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
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
	t.Cleanup(wsUpstream.Close)

	devDir, certDir := t.TempDir(), t.TempDir()
	certPath, keyPath := writeCert(t, certDir, "local", "app.local", 3)
	writeFile(t, filepath.Join(devDir, "app.yaml"), fmt.Sprintf(`
sites:
  - domains: [app.local]
    tls: {cert: %s, key: %s}
    routes:
      - {paths: [/], upstream: '%s'}
      - {paths: [/ws], upstream: '%s'}
`, certPath, keyPath, upstream.URL, wsUpstream.URL))
	writeFile(t, filepath.Join(devDir, "tunnel.yaml"), `
tunnel_client:
  server: tunnel.test
  token: `+testToken+`
  expose:
    - {domain: app.dev.test, site: app.local}
`)
	dev := NewProxy(Options{ConfigDir: devDir, PollInterval: time.Hour})
	// The test certificate is self-signed and the endpoint listens on a random port.
	dev.tunnelTLS = &tls.Config{InsecureSkipVerify: true}
	address := prodServer.Listener.Addr().String()
	dev.tunnelDial = func(ctx context.Context, network, _ string) (net.Conn, error) {
		return (&net.Dialer{}).DialContext(ctx, network, address)
	}
	dev.Reload()
	t.Cleanup(func() { dev.updateTunnelClient(nil) })

	waitFor(t, "the tunnel to connect", func() bool {
		return strings.Join(prod.tunnels.Domains(), " ") == "app.dev.test"
	})
	if !prod.knownHost("app.dev.test") {
		t.Fatal("the tunnel domain must be known to the production proxy")
	}

	request := httptest.NewRequest("GET", "https://app.dev.test/path?q=1", nil)
	request.RemoteAddr = "203.0.113.9:1000"
	response := httptest.NewRecorder()
	prod.HTTPSHandler().ServeHTTP(response, request)
	if response.Code != http.StatusOK {
		t.Fatalf("status %d: %s", response.Code, response.Body)
	}
	var got map[string]string
	if err := json.Unmarshal(response.Body.Bytes(), &got); err != nil {
		t.Fatal(err)
	}
	for key, want := range map[string]string{"host": "app.dev.test", "uri": "/path?q=1", "realip": "203.0.113.9", "xff": "203.0.113.9", "proto": "https"} {
		if got[key] != want {
			t.Errorf("%s: got %q, want %q", key, got[key], want)
		}
	}

	// An upgraded connection through the production proxy, the tunnel and the dev proxy.
	plain := httptest.NewServer(prod.HTTPSHandler())
	t.Cleanup(plain.Close)
	conn, err := net.Dial("tcp", plain.Listener.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	_ = conn.SetDeadline(time.Now().Add(5 * time.Second))
	fmt.Fprint(conn, "GET /ws HTTP/1.1\r\nHost: app.dev.test\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n")
	reader := bufio.NewReader(conn)
	upgrade, err := http.ReadResponse(reader, nil)
	if err != nil || upgrade.StatusCode != http.StatusSwitchingProtocols {
		t.Fatalf("upgrade: %v %v", upgrade, err)
	}
	fmt.Fprint(conn, "ping\n")
	if line, err := reader.ReadString('\n'); err != nil || line != "echo ping\n" {
		t.Fatalf("got %q, %v", line, err)
	}

	// A disconnected dev proxy: the domain is answered 502 and keeps its certificate.
	dev.updateTunnelClient(nil)
	waitFor(t, "the tunnel to disconnect", func() bool { return len(prod.tunnels.Domains()) == 0 })
	response = httptest.NewRecorder()
	prod.HTTPSHandler().ServeHTTP(response, httptest.NewRequest("GET", "https://app.dev.test/", nil))
	if response.Code != http.StatusBadGateway {
		t.Fatalf("disconnected: status %d", response.Code)
	}
	if config, ok := prod.tlsFor("app.dev.test"); !ok || !config.ACME {
		t.Fatal("a disconnected tunnel domain keeps its ACME certificate")
	}
}

func TestTunnelReconnectReplacesSession(t *testing.T) {
	registry := NewTunnelRegistry()
	first := &tunnel{client: "laptop", domains: []string{"a.dev.test"}}
	if _, err := registry.claim(first); err != nil {
		t.Fatal(err)
	}
	other := &tunnel{client: "desktop", domains: []string{"a.dev.test"}}
	if _, err := registry.claim(other); err == nil {
		t.Fatal("another client must not take the domain")
	}
	second := &tunnel{client: "laptop", domains: []string{"a.dev.test", "b.dev.test"}}
	replaced, err := registry.claim(second)
	if err != nil || len(replaced) != 1 || replaced[0] != first {
		t.Fatalf("replaced %v, %v", replaced, err)
	}
	// The old session closing later must not drop the domains of the new one.
	registry.release(first)
	if strings.Join(registry.Domains(), " ") != "a.dev.test b.dev.test" {
		t.Fatalf("domains %v", registry.Domains())
	}
}

func waitFor(t *testing.T, what string, condition func() bool) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for !condition() {
		if time.Now().After(deadline) {
			t.Fatalf("timed out waiting for %s", what)
		}
		time.Sleep(20 * time.Millisecond)
	}
}
