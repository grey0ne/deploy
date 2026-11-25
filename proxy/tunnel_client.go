package main

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"reflect"
	"strings"
	"time"

	"github.com/hashicorp/yamux"
)

const (
	tunnelHandshakeTimeout = 30 * time.Second
	tunnelMaxBackoff       = time.Minute
)

// tunnelClientRun is the running tunnel client of a dev proxy.
type tunnelClientRun struct {
	config *TunnelClient
	cancel context.CancelFunc
}

// updateTunnelClient starts, restarts or stops the tunnel client when its config changes.
func (p *Proxy) updateTunnelClient(config *TunnelClient) {
	p.tunnelMu.Lock()
	defer p.tunnelMu.Unlock()
	if p.tunnelRun != nil && reflect.DeepEqual(p.tunnelRun.config, config) {
		return
	}
	if p.tunnelRun != nil {
		p.tunnelRun.cancel()
		p.tunnelRun = nil
	}
	if config == nil {
		return
	}
	ctx, cancel := context.WithCancel(context.Background())
	p.tunnelRun = &tunnelClientRun{config: config, cancel: cancel}
	go p.runTunnelClient(ctx, config)
}

// runTunnelClient keeps the tunnel connected, reconnecting with a growing pause.
func (p *Proxy) runTunnelClient(ctx context.Context, config *TunnelClient) {
	backoff := time.Second
	for {
		started := time.Now()
		err := p.connectTunnel(ctx, config)
		if ctx.Err() != nil {
			return
		}
		slog.Warn("tunnel is not connected", "server", config.Address, "error", err, "retry", backoff)
		if time.Since(started) > tunnelMaxBackoff {
			// The tunnel was up for a while: a new problem, retry soon.
			backoff = time.Second
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(backoff):
		}
		backoff = min(backoff*2, tunnelMaxBackoff)
	}
}

// connectTunnel registers the exposed domains and serves the session until it closes.
func (p *Proxy) connectTunnel(ctx context.Context, config *TunnelClient) error {
	rawConn, err := p.tunnelDial(ctx, "tcp", config.Address)
	if err != nil {
		return err
	}
	tlsConfig := p.tunnelTLS.Clone()
	tlsConfig.ServerName = config.ServerName
	// Upgrade needs HTTP/1.1; the production proxy offers h2 too.
	tlsConfig.NextProtos = []string{"http/1.1"}
	tlsConfig.MinVersion = tls.VersionTLS12
	conn := tls.Client(rawConn, tlsConfig)
	_ = conn.SetDeadline(time.Now().Add(tunnelHandshakeTimeout))
	reader, err := tunnelHandshake(ctx, conn, config)
	if err != nil {
		conn.Close()
		return err
	}
	_ = conn.SetDeadline(time.Time{})
	session, err := yamux.Server(&bufferedConn{Conn: conn, reader: reader}, yamuxConfig())
	if err != nil {
		conn.Close()
		return err
	}
	slog.Info("tunnel connected", "server", config.Address, "domains", strings.Join(config.Domains(), " "))
	stop := context.AfterFunc(ctx, func() { _ = session.Close() })
	defer stop()
	server := &http.Server{
		Handler:           logRequests(p.tunnelLocalHandler(config)),
		ReadHeaderTimeout: 30 * time.Second,
		IdleTimeout:       120 * time.Second,
		ErrorLog:          slog.NewLogLogger(slog.Default().Handler(), slog.LevelDebug),
	}
	err = server.Serve(session)
	_ = session.Close()
	if ctx.Err() != nil {
		return ctx.Err()
	}
	return fmt.Errorf("session closed: %w", err)
}

func tunnelHandshake(ctx context.Context, conn *tls.Conn, config *TunnelClient) (*bufio.Reader, error) {
	if err := conn.HandshakeContext(ctx); err != nil {
		return nil, err
	}
	body, err := json.Marshal(tunnelHello{Domains: config.Domains()})
	if err != nil {
		return nil, err
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodPost, "https://"+config.ServerName+tunnelConnectPath, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	request.Header.Set("Authorization", "Bearer "+config.Token)
	request.Header.Set("Connection", "Upgrade")
	request.Header.Set("Upgrade", tunnelProtocol)
	request.Header.Set("Content-Type", "application/json")
	if err := request.Write(conn); err != nil {
		return nil, err
	}
	reader := bufio.NewReader(conn)
	response, err := http.ReadResponse(reader, request)
	if err != nil {
		return nil, err
	}
	if response.StatusCode != http.StatusSwitchingProtocols {
		message, _ := io.ReadAll(io.LimitReader(response.Body, 4<<10))
		response.Body.Close()
		return nil, fmt.Errorf("rejected by the server: %s: %s", response.Status, strings.TrimSpace(string(message)))
	}
	return reader, nil
}

// tunnelLocalHandler serves the requests of the production proxy with the local sites.
func (p *Proxy) tunnelLocalHandler(config *TunnelClient) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host := requestHost(r)
		local, ok := config.Expose[host]
		if !ok {
			http.Error(w, "the domain is not exposed", http.StatusNotFound)
			return
		}
		site := p.site(local)
		if site == nil {
			http.Error(w, fmt.Sprintf("local site %s is not configured", local), http.StatusBadGateway)
			return
		}
		// The production proxy is the only client of the session: it sends the visitor's address.
		if ip := net.ParseIP(r.Header.Get("X-Real-IP")); ip != nil {
			r.RemoteAddr = net.JoinHostPort(ip.String(), "0")
		}
		site.ServeHTTP(w, r)
	})
}

// warnMissingTunnelSites logs exposed domains whose local site is not configured.
func warnMissingTunnelSites(table *Table) {
	if table.tunnelClient == nil {
		return
	}
	for domain, local := range table.tunnelClient.Expose {
		if table.hosts[local] == nil {
			slog.Warn("exposed domain has no local site", "domain", domain, "site", local)
		}
	}
}
