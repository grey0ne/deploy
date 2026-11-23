package main

import (
	"crypto/tls"
	"fmt"
	"log/slog"
	"os"
	"sync"
	"time"
)

// CertFiles keeps certificate/key pairs loaded from files and reloads a pair when either file
// changes, so renewed certificates are served without a restart. A pair that fails to load
// (e.g. the certificate is already rewritten and the key is not yet) keeps the previous one
// and is retried on the next check.
type CertFiles struct {
	mu    sync.RWMutex
	pairs map[certKey]*certPair
}

type certKey struct{ cert, key string }

type certPair struct {
	cert    *tls.Certificate
	certMod time.Time
	keyMod  time.Time
}

func NewCertFiles() *CertFiles {
	return &CertFiles{pairs: map[certKey]*certPair{}}
}

// Load makes sure the pair is loaded; an error means it never loaded successfully.
func (c *CertFiles) Load(certFile, keyFile string) error {
	key := certKey{certFile, keyFile}
	c.mu.RLock()
	_, ok := c.pairs[key]
	c.mu.RUnlock()
	if ok {
		return nil
	}
	pair, err := loadPair(key)
	if err != nil {
		return err
	}
	c.mu.Lock()
	c.pairs[key] = pair
	c.mu.Unlock()
	return nil
}

func (c *CertFiles) Get(certFile, keyFile string) (*tls.Certificate, error) {
	c.mu.RLock()
	pair, ok := c.pairs[certKey{certFile, keyFile}]
	c.mu.RUnlock()
	if !ok {
		return nil, fmt.Errorf("certificate %s is not loaded", certFile)
	}
	return pair.cert, nil
}

// Refresh reloads changed pairs and forgets the pairs no site uses any more.
func (c *CertFiles) Refresh(used map[certKey]bool) {
	c.mu.RLock()
	var changed []certKey
	var unused []certKey
	for key, pair := range c.pairs {
		if !used[key] {
			unused = append(unused, key)
			continue
		}
		certMod, keyMod, err := modTimes(key)
		if err != nil || !certMod.Equal(pair.certMod) || !keyMod.Equal(pair.keyMod) {
			changed = append(changed, key)
		}
	}
	c.mu.RUnlock()
	for _, key := range changed {
		pair, err := loadPair(key)
		if err != nil {
			slog.Error("certificate reload failed, the previous one is served", "cert", key.cert, "error", err)
			continue
		}
		slog.Info("certificate reloaded", "cert", key.cert)
		c.mu.Lock()
		c.pairs[key] = pair
		c.mu.Unlock()
	}
	if len(unused) > 0 {
		c.mu.Lock()
		for _, key := range unused {
			delete(c.pairs, key)
		}
		c.mu.Unlock()
	}
}

func modTimes(key certKey) (time.Time, time.Time, error) {
	certInfo, err := os.Stat(key.cert)
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	keyInfo, err := os.Stat(key.key)
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	return certInfo.ModTime(), keyInfo.ModTime(), nil
}

func loadPair(key certKey) (*certPair, error) {
	// Times first: a write between the stat and the read is picked up by the next check.
	certMod, keyMod, err := modTimes(key)
	if err != nil {
		return nil, err
	}
	cert, err := tls.LoadX509KeyPair(key.cert, key.key)
	if err != nil {
		return nil, err
	}
	return &certPair{cert: &cert, certMod: certMod, keyMod: keyMod}, nil
}
