package main

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

// loadDir reads every config file of the directory. A file that fails to parse, references a
// certificate that does not load or claims a domain of an earlier file (in name order) keeps
// its previously accepted version, or is skipped when it has none; the other files are not
// affected. The returned errors describe every such file. err is set when the directory
// itself cannot be read: then nothing changes.
func loadDir(dir string, previous map[string][]Site, certs *CertFiles) (accepted map[string][]Site, errs []error, err error) {
	names, err := configFiles(dir)
	if err != nil {
		return nil, nil, err
	}
	accepted = map[string][]Site{}
	owners := map[string]string{} // domain -> file
	accept := func(name string, sites []Site) {
		for _, site := range sites {
			for _, domain := range site.Domains {
				owners[domain] = name
			}
		}
		accepted[name] = sites
	}
	for _, name := range names {
		sites, err := readFile(filepath.Join(dir, name), certs)
		if err == nil {
			err = domainConflict(sites, owners)
		}
		if err == nil {
			accept(name, sites)
			continue
		}
		if old, ok := previous[name]; ok && domainConflict(old, owners) == nil {
			accept(name, old)
			errs = append(errs, fmt.Errorf("%s: %w (the previous version is served)", name, err))
		} else {
			errs = append(errs, fmt.Errorf("%s: %w (not served)", name, err))
		}
	}
	return accepted, errs, nil
}

func readFile(path string, certs *CertFiles) ([]Site, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	sites, err := ParseFile(data)
	if err != nil {
		return nil, err
	}
	for _, site := range sites {
		if site.TLS.ACME {
			continue
		}
		if err := certs.Load(site.TLS.Cert, site.TLS.Key); err != nil {
			return nil, fmt.Errorf("%s: certificate: %w", site.Domains[0], err)
		}
	}
	return sites, nil
}

func domainConflict(sites []Site, owners map[string]string) error {
	var taken []string
	for _, site := range sites {
		for _, domain := range site.Domains {
			if owner, ok := owners[domain]; ok {
				taken = append(taken, fmt.Sprintf("%s (in %s)", domain, owner))
			}
		}
	}
	if taken != nil {
		return fmt.Errorf("domains already served by another file: %s", strings.Join(taken, ", "))
	}
	return nil
}
