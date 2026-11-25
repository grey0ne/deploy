package main

import (
	"errors"
	"fmt"
	"net"
	"sort"
	"strings"
)

// TunnelServerConfig: the production proxy accepts dev proxies that expose local sites on
// public domains. At most one file of the directory has this section.
type TunnelServerConfig struct {
	// Domain the dev proxies connect to (https://<domain>/connect).
	Domain  string               `yaml:"domain"`
	TLS     TLSConfig            `yaml:"tls"`
	Clients []TunnelAccessConfig `yaml:"clients"`
}

type TunnelAccessConfig struct {
	Name  string `yaml:"name"`
	Token string `yaml:"token"`
	// Public domains the client may serve: exact names or "*.suffix" (any subdomain).
	Domains []string `yaml:"domains"`
}

// TunnelClientConfig: the dev proxy connects to the production proxy and serves the exposed
// public domains with the routes of local sites. At most one file of the directory has it.
type TunnelClientConfig struct {
	// host[:port] of the tunnel endpoint (tunnel_server.domain), port 443 by default.
	Server string               `yaml:"server"`
	Token  string               `yaml:"token"`
	Expose []TunnelExposeConfig `yaml:"expose"`
}

type TunnelExposeConfig struct {
	// Public domain served by the production proxy.
	Domain string `yaml:"domain"`
	// Domain of the local site whose routes serve it.
	Site string `yaml:"site"`
}

// Tokens shorter than this are rejected: they would be guessable.
const minTunnelTokenLength = 32

type TunnelServer struct {
	Domain  string
	TLS     TLSConfig
	Clients []TunnelAccess
}

type TunnelAccess struct {
	Name     string
	Token    string
	exact    map[string]bool
	suffixes []string // ".dev.example.com" for "*.dev.example.com"
}

func (a TunnelAccess) allows(domain string) bool {
	if a.exact[domain] {
		return true
	}
	for _, suffix := range a.suffixes {
		if strings.HasSuffix(domain, suffix) && len(domain) > len(suffix) {
			return true
		}
	}
	return false
}

type TunnelClient struct {
	Address    string // host:port
	ServerName string
	Token      string
	// Public domain -> local site domain.
	Expose map[string]string
}

// Domains are the public domains in a stable order.
func (c *TunnelClient) Domains() []string {
	domains := make([]string, 0, len(c.Expose))
	for domain := range c.Expose {
		domains = append(domains, domain)
	}
	sort.Strings(domains)
	return domains
}

func validateDomain(value string) (string, error) {
	domain := normalizeHost(value)
	if !domainRE.MatchString(domain) {
		return "", fmt.Errorf("invalid domain %q", value)
	}
	return domain, nil
}

func validateTunnelServer(config TunnelServerConfig) (*TunnelServer, error) {
	domain, err := validateDomain(config.Domain)
	if err != nil {
		return nil, fmt.Errorf("domain: %w", err)
	}
	server := &TunnelServer{Domain: domain, TLS: config.TLS}
	if config.TLS.ACME == (config.TLS.Cert != "" || config.TLS.Key != "") {
		return nil, errors.New("tls: set either acme: true or cert and key")
	}
	if !config.TLS.ACME && (config.TLS.Cert == "" || config.TLS.Key == "") {
		return nil, errors.New("tls: both cert and key are required")
	}
	if len(config.Clients) == 0 {
		return nil, errors.New("clients: at least one client is required")
	}
	names, tokens := map[string]bool{}, map[string]bool{}
	for i, clientConfig := range config.Clients {
		access, err := validateTunnelAccess(clientConfig)
		if err != nil {
			return nil, fmt.Errorf("clients[%d]: %w", i, err)
		}
		if names[access.Name] || tokens[access.Token] {
			return nil, fmt.Errorf("clients[%d]: the name and the token must be unique", i)
		}
		names[access.Name], tokens[access.Token] = true, true
		server.Clients = append(server.Clients, access)
	}
	return server, nil
}

func validateTunnelAccess(config TunnelAccessConfig) (TunnelAccess, error) {
	access := TunnelAccess{Name: config.Name, Token: config.Token, exact: map[string]bool{}}
	if config.Name == "" {
		return access, errors.New("name is required")
	}
	if len(config.Token) < minTunnelTokenLength {
		return access, fmt.Errorf("token: at least %d characters are required", minTunnelTokenLength)
	}
	if len(config.Domains) == 0 {
		return access, errors.New("domains: at least one domain or *.suffix pattern is required")
	}
	for _, pattern := range config.Domains {
		suffix, wildcard := strings.CutPrefix(pattern, "*.")
		domain, err := validateDomain(suffix)
		if err != nil || (wildcard && !strings.Contains(domain, ".")) {
			return access, fmt.Errorf("domains: %q must be a domain or *.<domain with a dot>", pattern)
		}
		if wildcard {
			access.suffixes = append(access.suffixes, "."+domain)
		} else {
			access.exact[domain] = true
		}
	}
	return access, nil
}

func validateTunnelClient(config TunnelClientConfig) (*TunnelClient, error) {
	host, port, err := net.SplitHostPort(config.Server)
	if err != nil {
		host, port = config.Server, "443"
	}
	serverName, err := validateDomain(host)
	if err != nil {
		return nil, fmt.Errorf("server: %w", err)
	}
	client := &TunnelClient{
		Address:    net.JoinHostPort(serverName, port),
		ServerName: serverName,
		Token:      config.Token,
		Expose:     map[string]string{},
	}
	if len(config.Token) < minTunnelTokenLength {
		return nil, fmt.Errorf("token: at least %d characters are required", minTunnelTokenLength)
	}
	if len(config.Expose) == 0 {
		return nil, errors.New("expose: at least one site is required")
	}
	for i, expose := range config.Expose {
		domain, err := validateDomain(expose.Domain)
		if err != nil {
			return nil, fmt.Errorf("expose[%d].domain: %w", i, err)
		}
		site, err := validateDomain(expose.Site)
		if err != nil {
			return nil, fmt.Errorf("expose[%d].site: %w", i, err)
		}
		if _, ok := client.Expose[domain]; ok {
			return nil, fmt.Errorf("expose[%d]: domain %s is exposed twice", i, domain)
		}
		client.Expose[domain] = site
	}
	return client, nil
}
