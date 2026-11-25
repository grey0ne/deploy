package main

import (
	"context"
	"crypto/tls"
	"errors"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"golang.org/x/crypto/acme"
	"golang.org/x/crypto/acme/autocert"
)

type Options struct {
	ConfigDir     string
	PollInterval  time.Duration
	ACMECache     string // empty: ACME is disabled, sites with acme: true get no certificate
	ACMEEmail     string
	ACMEDirectory string
}

// Proxy serves the sites of the config directory. The directory and the certificate files are
// checked every PollInterval (and on SIGHUP); changes are applied without a restart.
type Proxy struct {
	options    Options
	table      atomic.Pointer[Table]
	certs      *CertFiles
	transports *Transports
	acme       *autocert.Manager
	// Connected dev proxies (production side of the tunnel).
	tunnels *TunnelRegistry

	reloadMu sync.Mutex
	accepted map[string]*ParsedFile
	dirState string

	// Dev side of the tunnel; the dialer and the TLS config are replaced in tests.
	tunnelMu   sync.Mutex
	tunnelRun  *tunnelClientRun
	tunnelTLS  *tls.Config
	tunnelDial func(ctx context.Context, network, address string) (net.Conn, error)

	warmMu  sync.Mutex
	warmed  map[string]bool
	serving atomic.Bool
}

func NewProxy(options Options) *Proxy {
	p := &Proxy{
		options:    options,
		certs:      NewCertFiles(),
		transports: NewTransports(),
		warmed:     map[string]bool{},
		tunnels:    NewTunnelRegistry(),
		tunnelTLS:  &tls.Config{},
		tunnelDial: (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
	}
	p.tunnels.onConnect = p.warmDomains
	p.table.Store(&Table{hosts: map[string]*siteHandler{}})
	if options.ACMECache != "" {
		p.acme = &autocert.Manager{
			Prompt:     autocert.AcceptTOS,
			Cache:      autocert.DirCache(options.ACMECache),
			HostPolicy: p.acmeHostPolicy,
			Email:      options.ACMEEmail,
		}
		if options.ACMEDirectory != "" {
			p.acme.Client = &acme.Client{DirectoryURL: options.ACMEDirectory}
		}
	}
	return p
}

// Reload rereads the config directory and swaps the routing table.
func (p *Proxy) Reload() {
	p.reloadMu.Lock()
	defer p.reloadMu.Unlock()
	p.dirState = dirState(p.options.ConfigDir)
	accepted, errs, err := loadDir(p.options.ConfigDir, p.accepted, p.certs)
	if err != nil {
		slog.Error("config directory is not readable, the current config is kept", "error", err)
		return
	}
	for _, err := range errs {
		slog.Error("config file rejected", "error", err)
	}
	p.accepted = accepted
	table := buildTable(accepted, p.transports)
	p.table.Store(table)
	p.certs.Refresh(usedCerts(table))
	slog.Info("config loaded", "files", len(accepted), "domains", strings.Join(table.domains(), " "))
	if table.tunnelServer != nil {
		slog.Info("tunnel server", "domain", table.tunnelServer.Domain, "clients", len(table.tunnelServer.Clients))
	}
	warnMissingTunnelSites(table)
	p.updateTunnelClient(table.tunnelClient)
	if p.serving.Load() {
		p.warmACME()
	}
}

// Watch reloads the config when a file of the directory changes and reloads changed
// certificate files.
func (p *Proxy) Watch(ctx context.Context, reload <-chan os.Signal) {
	ticker := time.NewTicker(p.options.PollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-reload:
			slog.Info("reload requested")
			p.Reload()
		case <-ticker.C:
			p.reloadMu.Lock()
			changed := dirState(p.options.ConfigDir) != p.dirState
			p.reloadMu.Unlock()
			if changed {
				p.Reload()
			} else {
				p.certs.Refresh(usedCerts(p.table.Load()))
			}
		}
	}
}

// dirState changes whenever a config file is added, removed or rewritten.
func dirState(dir string) string {
	names, err := configFiles(dir)
	if err != nil {
		return "error: " + err.Error()
	}
	var state strings.Builder
	for _, name := range names {
		info, err := os.Stat(filepath.Join(dir, name))
		if err != nil {
			continue
		}
		fmt.Fprintf(&state, "%s %d %d\n", name, info.Size(), info.ModTime().UnixNano())
	}
	return state.String()
}

func usedCerts(table *Table) map[certKey]bool {
	used := map[certKey]bool{}
	for _, site := range table.hosts {
		if !site.TLS.ACME {
			used[certKey{site.TLS.Cert, site.TLS.Key}] = true
		}
	}
	if server := table.tunnelServer; server != nil && !server.TLS.ACME {
		used[certKey{server.TLS.Cert, server.TLS.Key}] = true
	}
	return used
}

func (t *Table) domains() []string {
	domains := make([]string, 0, len(t.hosts))
	for domain := range t.hosts {
		domains = append(domains, domain)
	}
	sort.Strings(domains)
	return domains
}

func (p *Proxy) site(host string) *siteHandler {
	return p.table.Load().hosts[host]
}

// tunnelServer returns the tunnel endpoint config when the host is its domain.
func (p *Proxy) tunnelServer(host string) *TunnelServer {
	if server := p.table.Load().tunnelServer; server != nil && server.Domain == host {
		return server
	}
	return nil
}

// knownHost: a site, the tunnel endpoint or a domain of a tunnel.
func (p *Proxy) knownHost(host string) bool {
	return p.site(host) != nil || p.tunnelServer(host) != nil || p.tunnels.known(host)
}

// tlsFor returns the TLS settings of the host; tunnel domains always use ACME.
func (p *Proxy) tlsFor(host string) (TLSConfig, bool) {
	if site := p.site(host); site != nil {
		return site.TLS, true
	}
	if server := p.tunnelServer(host); server != nil {
		return server.TLS, true
	}
	if p.tunnels.known(host) {
		return TLSConfig{ACME: true}, true
	}
	return TLSConfig{}, false
}

func (p *Proxy) acmeHostPolicy(_ context.Context, host string) error {
	if config, ok := p.tlsFor(normalizeHost(host)); ok && config.ACME {
		return nil
	}
	return fmt.Errorf("%s is not an ACME domain of the config", host)
}

func (p *Proxy) GetCertificate(hello *tls.ClientHelloInfo) (*tls.Certificate, error) {
	config, ok := p.tlsFor(normalizeHost(hello.ServerName))
	if !ok {
		return nil, fmt.Errorf("unknown server name %q", hello.ServerName)
	}
	if config.ACME {
		if p.acme == nil {
			return nil, errors.New("ACME is disabled (no -acme-cache)")
		}
		return p.acme.GetCertificate(hello)
	}
	return p.certs.Get(config.Cert, config.Key)
}

func (p *Proxy) TLSConfig() *tls.Config {
	protos := []string{"h2", "http/1.1"}
	if p.acme != nil {
		protos = append(protos, acme.ALPNProto) // tls-alpn-01 challenges
	}
	return &tls.Config{
		MinVersion:     tls.VersionTLS12,
		GetCertificate: p.GetCertificate,
		NextProtos:     protos,
	}
}

// HTTPSHandler serves the sites, the tunnel endpoint and the domains of the tunnels. The
// sites of the config win over tunnels, also when a site appears after a tunnel connected.
func (p *Proxy) HTTPSHandler() http.Handler {
	return logRequests(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host := requestHost(r)
		if site := p.site(host); site != nil {
			site.ServeHTTP(w, r)
			return
		}
		if server := p.tunnelServer(host); server != nil {
			p.tunnels.ServeConnect(w, r, server, func(domain string) bool { return p.site(domain) != nil })
			return
		}
		if p.tunnels.known(host) {
			p.tunnels.ServeDomain(w, r, host)
			return
		}
		http.Error(w, "unknown host", http.StatusMisdirectedRequest)
	}))
}

// HTTPHandler answers ACME http-01 challenges and redirects the sites to https.
func (p *Proxy) HTTPHandler() http.Handler {
	var handler http.Handler = http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !p.knownHost(requestHost(r)) {
			http.Error(w, "unknown host", http.StatusNotFound)
			return
		}
		redirectToHTTPS(w, r)
	})
	if p.acme != nil {
		handler = p.acme.HTTPHandler(handler)
	}
	return logRequests(handler)
}

// StartServing enables certificate warm-up: the http-01 challenge needs the http listener.
func (p *Proxy) StartServing() {
	p.serving.Store(true)
	p.warmACME()
}

// warmACME requests the certificates of new ACME domains in the background, so the first
// visitor does not wait for the issuance and a failure (e.g. DNS not pointing here) is
// logged right after a deploy. A failed domain is retried on the next reload.
func (p *Proxy) warmACME() {
	table := p.table.Load()
	var domains []string
	for domain, site := range table.hosts {
		if site.TLS.ACME {
			domains = append(domains, domain)
		}
	}
	if server := table.tunnelServer; server != nil && server.TLS.ACME {
		domains = append(domains, server.Domain)
	}
	p.warmDomains(domains)
}

func (p *Proxy) warmDomains(domains []string) {
	if p.acme == nil {
		return
	}
	for _, domain := range domains {
		p.warmMu.Lock()
		started := p.warmed[domain]
		p.warmed[domain] = true
		p.warmMu.Unlock()
		if started {
			continue
		}
		go func() {
			// Looks like a modern client: autocert issues an ECDSA certificate for it.
			_, err := p.acme.GetCertificate(&tls.ClientHelloInfo{
				ServerName:       domain,
				CipherSuites:     []uint16{tls.TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256},
				SupportedCurves:  []tls.CurveID{tls.CurveP256},
				SignatureSchemes: []tls.SignatureScheme{tls.ECDSAWithP256AndSHA256},
			})
			if err != nil {
				slog.Error("certificate is not available", "domain", domain, "error", err)
				p.warmMu.Lock()
				delete(p.warmed, domain)
				p.warmMu.Unlock()
				return
			}
			slog.Info("certificate is ready", "domain", domain)
		}()
	}
}
