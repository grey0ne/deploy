package main

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"net/url"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"

	"go.yaml.in/yaml/v3"
)

// FileConfig is one YAML file of the config directory. Every project owns one file,
// so a broken file never affects the sites of the other files.
type FileConfig struct {
	Sites        []SiteConfig        `yaml:"sites"`
	TunnelServer *TunnelServerConfig `yaml:"tunnel_server"`
	TunnelClient *TunnelClientConfig `yaml:"tunnel_client"`
}

// ParsedFile is a validated FileConfig.
type ParsedFile struct {
	Sites        []Site
	TunnelServer *TunnelServer
	TunnelClient *TunnelClient
}

type SiteConfig struct {
	Domains []string  `yaml:"domains"`
	TLS     TLSConfig `yaml:"tls"`
	// nginx size syntax: "50000k", "100m", "1g" or bytes; default 1m.
	MaxBodySize string `yaml:"max_body_size"`
	// gzip text responses the upstream left uncompressed; default true.
	Compress *bool         `yaml:"compress"`
	Routes   []RouteConfig `yaml:"routes"`
}

// TLSConfig: either a Let's Encrypt certificate (acme) or certificate files, reread when they change.
type TLSConfig struct {
	ACME bool   `yaml:"acme"`
	Cert string `yaml:"cert"`
	Key  string `yaml:"key"`
}

type RouteConfig struct {
	// Path prefixes matched by whole segments: "/api" matches "/api" and "/api/x", not "/apix".
	// The longest matching prefix of the site wins; "/" matches everything.
	Paths    []string `yaml:"paths"`
	Upstream string   `yaml:"upstream"`
	// Time to wait for the upstream response headers; default 60s. Responses without a length
	// (server-sent events, streaming) reach the client as they are produced.
	Timeout string `yaml:"timeout"`
}

const (
	defaultMaxBodySize = 1 << 20
	defaultTimeout     = 60 * time.Second
)

var (
	domainRE = regexp.MustCompile(`^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)*[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$`)
	sizeRE   = regexp.MustCompile(`^([1-9][0-9]*)([kKmMgG]?)$`)
)

// Site is a validated SiteConfig.
type Site struct {
	Domains     []string
	TLS         TLSConfig
	MaxBodySize int64
	Compress    bool
	Routes      []Route
}

type Route struct {
	Path     string
	Upstream *url.URL
	Timeout  time.Duration
}

// ParseFile decodes and validates one config file. Unknown fields are errors: a typo must not
// silently change the routing of a site.
func ParseFile(data []byte) (*ParsedFile, error) {
	var config FileConfig
	decoder := yaml.NewDecoder(bytes.NewReader(data))
	decoder.KnownFields(true)
	// An empty file has no sites.
	if err := decoder.Decode(&config); err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}
	parsed := &ParsedFile{Sites: make([]Site, 0, len(config.Sites))}
	domains := map[string]bool{}
	for i, siteConfig := range config.Sites {
		site, err := validateSite(siteConfig)
		if err != nil {
			return nil, fmt.Errorf("sites[%d]: %w", i, err)
		}
		for _, domain := range site.Domains {
			if domains[domain] {
				return nil, fmt.Errorf("sites[%d]: domain %s is listed twice", i, domain)
			}
			domains[domain] = true
		}
		parsed.Sites = append(parsed.Sites, site)
	}
	if config.TunnelServer != nil {
		server, err := validateTunnelServer(*config.TunnelServer)
		if err != nil {
			return nil, fmt.Errorf("tunnel_server: %w", err)
		}
		if domains[server.Domain] {
			return nil, fmt.Errorf("tunnel_server: domain %s is also a site", server.Domain)
		}
		parsed.TunnelServer = server
	}
	if config.TunnelClient != nil {
		client, err := validateTunnelClient(*config.TunnelClient)
		if err != nil {
			return nil, fmt.Errorf("tunnel_client: %w", err)
		}
		parsed.TunnelClient = client
	}
	return parsed, nil
}

// Domains are the domains the file claims on this proxy: its sites and the tunnel endpoint.
func (f *ParsedFile) Domains() []string {
	var domains []string
	for _, site := range f.Sites {
		domains = append(domains, site.Domains...)
	}
	if f.TunnelServer != nil {
		domains = append(domains, f.TunnelServer.Domain)
	}
	return domains
}

func validateSite(config SiteConfig) (Site, error) {
	site := Site{TLS: config.TLS, Compress: true, MaxBodySize: defaultMaxBodySize}
	if len(config.Domains) == 0 {
		return site, errors.New("domains: at least one domain is required")
	}
	for _, domain := range config.Domains {
		normalized := normalizeHost(domain)
		if !domainRE.MatchString(normalized) {
			return site, fmt.Errorf("domains: invalid domain %q", domain)
		}
		site.Domains = append(site.Domains, normalized)
	}
	if config.TLS.ACME == (config.TLS.Cert != "" || config.TLS.Key != "") {
		return site, errors.New("tls: set either acme: true or cert and key")
	}
	if !config.TLS.ACME && (config.TLS.Cert == "" || config.TLS.Key == "") {
		return site, errors.New("tls: both cert and key are required")
	}
	if config.MaxBodySize != "" {
		size, err := parseSize(config.MaxBodySize)
		if err != nil {
			return site, fmt.Errorf("max_body_size: %w", err)
		}
		site.MaxBodySize = size
	}
	if config.Compress != nil {
		site.Compress = *config.Compress
	}
	if len(config.Routes) == 0 {
		return site, errors.New("routes: at least one route is required")
	}
	paths := map[string]bool{}
	for i, routeConfig := range config.Routes {
		routes, err := validateRoute(routeConfig)
		if err != nil {
			return site, fmt.Errorf("routes[%d]: %w", i, err)
		}
		for _, route := range routes {
			if paths[route.Path] {
				return site, fmt.Errorf("routes[%d]: path %s is routed twice", i, route.Path)
			}
			paths[route.Path] = true
		}
		site.Routes = append(site.Routes, routes...)
	}
	// Longest prefix first: the first match is the most specific one.
	sort.SliceStable(site.Routes, func(a, b int) bool {
		return len(site.Routes[a].Path) > len(site.Routes[b].Path)
	})
	return site, nil
}

func validateRoute(config RouteConfig) ([]Route, error) {
	if len(config.Paths) == 0 {
		return nil, errors.New("paths: at least one path is required")
	}
	upstream, err := url.Parse(config.Upstream)
	if err != nil || (upstream.Scheme != "http" && upstream.Scheme != "https") || upstream.Host == "" {
		return nil, fmt.Errorf("upstream: an http(s)://host:port URL is required, got %q", config.Upstream)
	}
	if (upstream.Path != "" && upstream.Path != "/") || upstream.RawQuery != "" || upstream.User != nil {
		return nil, fmt.Errorf("upstream: only scheme, host and port are allowed, got %q", config.Upstream)
	}
	upstream.Path = ""
	timeout := defaultTimeout
	if config.Timeout != "" {
		timeout, err = time.ParseDuration(config.Timeout)
		if err != nil || timeout <= 0 {
			return nil, fmt.Errorf("timeout: a positive duration like 120s is required, got %q", config.Timeout)
		}
	}
	routes := make([]Route, 0, len(config.Paths))
	for _, routePath := range config.Paths {
		if !strings.HasPrefix(routePath, "/") || path.Clean(routePath) != routePath {
			return nil, fmt.Errorf("paths: %q must be a clean absolute path like /api, without a trailing slash", routePath)
		}
		routes = append(routes, Route{
			Path:     routePath,
			Upstream: upstream,
			Timeout:  timeout,
		})
	}
	return routes, nil
}

// parseSize reads the nginx size syntax: k, m and g are powers of 1024.
func parseSize(value string) (int64, error) {
	match := sizeRE.FindStringSubmatch(value)
	if match == nil {
		return 0, fmt.Errorf("a size like 100m or 50000k is required, got %q", value)
	}
	size, err := strconv.ParseInt(match[1], 10, 64)
	if err != nil {
		return 0, err
	}
	multiplier := map[string]int64{"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}[strings.ToLower(match[2])]
	if size > (1<<62)/multiplier {
		return 0, fmt.Errorf("%q is too large", value)
	}
	return size * multiplier, nil
}

func normalizeHost(host string) string {
	return strings.TrimSuffix(strings.ToLower(host), ".")
}

// configFiles lists the YAML files of the directory in name order; hidden files are skipped,
// so a file can be written next to the others and renamed into place atomically.
func configFiles(dir string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	var names []string
	for _, entry := range entries {
		name := entry.Name()
		ext := filepath.Ext(name)
		if entry.IsDir() || strings.HasPrefix(name, ".") || (ext != ".yaml" && ext != ".yml") {
			continue
		}
		names = append(names, name)
	}
	sort.Strings(names)
	return names, nil
}
