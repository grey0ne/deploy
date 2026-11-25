package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// loadDir reads every config file of the directory. A file that fails to parse, references a
// certificate that does not load, claims a domain of an earlier file (in name order) or
// repeats a tunnel section of an earlier file keeps its previously accepted version, or is
// skipped when it has none; the other files are not affected. The returned errors describe
// every such file. err is set when the directory itself cannot be read: then nothing changes.
func loadDir(dir string, previous map[string]*ParsedFile, certs *CertFiles) (accepted map[string]*ParsedFile, errs []error, err error) {
	names, err := configFiles(dir)
	if err != nil {
		return nil, nil, err
	}
	accepted = map[string]*ParsedFile{}
	owners := &fileOwners{domains: map[string]string{}}
	accept := func(name string, file *ParsedFile) {
		owners.add(name, file)
		accepted[name] = file
	}
	for _, name := range names {
		file, err := readFile(filepath.Join(dir, name), certs)
		if err == nil {
			err = owners.conflict(file)
		}
		if err == nil {
			accept(name, file)
			continue
		}
		if old, ok := previous[name]; ok && owners.conflict(old) == nil {
			accept(name, old)
			errs = append(errs, fmt.Errorf("%s: %w (the previous version is served)", name, err))
		} else {
			errs = append(errs, fmt.Errorf("%s: %w (not served)", name, err))
		}
	}
	return accepted, errs, nil
}

func readFile(path string, certs *CertFiles) (*ParsedFile, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	file, err := ParseFile(data)
	if err != nil {
		return nil, err
	}
	for _, site := range file.Sites {
		if site.TLS.ACME {
			continue
		}
		if err := certs.Load(site.TLS.Cert, site.TLS.Key); err != nil {
			return nil, fmt.Errorf("%s: certificate: %w", site.Domains[0], err)
		}
	}
	if server := file.TunnelServer; server != nil && !server.TLS.ACME {
		if err := certs.Load(server.TLS.Cert, server.TLS.Key); err != nil {
			return nil, fmt.Errorf("tunnel_server: certificate: %w", err)
		}
	}
	return file, nil
}

// fileOwners records which accepted file claims every domain and the tunnel sections.
type fileOwners struct {
	domains      map[string]string
	tunnelServer string
	tunnelClient string
}

func (o *fileOwners) add(name string, file *ParsedFile) {
	for _, domain := range file.Domains() {
		o.domains[domain] = name
	}
	if file.TunnelServer != nil {
		o.tunnelServer = name
	}
	if file.TunnelClient != nil {
		o.tunnelClient = name
	}
}

func (o *fileOwners) conflict(file *ParsedFile) error {
	var taken []string
	for _, domain := range file.Domains() {
		if owner, ok := o.domains[domain]; ok {
			taken = append(taken, fmt.Sprintf("%s (in %s)", domain, owner))
		}
	}
	if taken != nil {
		return fmt.Errorf("domains already served by another file: %s", strings.Join(taken, ", "))
	}
	if file.TunnelServer != nil && o.tunnelServer != "" {
		return fmt.Errorf("tunnel_server is already configured in %s", o.tunnelServer)
	}
	if file.TunnelClient != nil && o.tunnelClient != "" {
		return fmt.Errorf("tunnel_client is already configured in %s", o.tunnelClient)
	}
	return nil
}
