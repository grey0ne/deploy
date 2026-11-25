// Command proxy is the reverse proxy in front of the projects: TLS termination with Let's
// Encrypt or certificate files, routing by domain and path prefix to upstream containers.
// Sites are configured by the YAML files of a directory (one per project); config and
// certificate changes are applied without a restart.
package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

const version = "0.2.0"

func main() {
	var options Options
	httpAddr := flag.String("http", ":80", "address of the http listener (redirects, ACME challenges)")
	httpsAddr := flag.String("https", ":443", "address of the https listener")
	flag.StringVar(&options.ConfigDir, "config", "/config", "directory with the *.yaml site files")
	flag.DurationVar(&options.PollInterval, "poll", 2*time.Second, "how often the config and certificate files are checked")
	flag.StringVar(&options.ACMECache, "acme-cache", "", "directory for Let's Encrypt accounts and certificates; empty disables ACME")
	flag.StringVar(&options.ACMEEmail, "acme-email", "", "contact email of the Let's Encrypt account")
	flag.StringVar(&options.ACMEDirectory, "acme-directory", "", "ACME directory URL (default: Let's Encrypt production)")
	check := flag.Bool("check", false, "validate the config directory and exit")
	showVersion := flag.Bool("version", false, "print the version and exit")
	flag.Parse()

	if *showVersion {
		fmt.Println(version)
		return
	}
	if *check {
		os.Exit(runCheck(options.ConfigDir))
	}
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stdout, nil)))
	if err := run(options, *httpAddr, *httpsAddr); err != nil {
		slog.Error("proxy stopped", "error", err)
		os.Exit(1)
	}
}

// runCheck validates every file strictly: any rejected file is an error.
func runCheck(dir string) int {
	accepted, errs, err := loadDir(dir, nil, NewCertFiles())
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	for _, err := range errs {
		fmt.Fprintln(os.Stderr, err)
	}
	if len(errs) > 0 {
		return 1
	}
	sites := 0
	for _, file := range accepted {
		sites += len(file.Sites)
	}
	fmt.Printf("config is valid: %d files, %d sites\n", len(accepted), sites)
	return 0
}

func run(options Options, httpAddr, httpsAddr string) error {
	proxy := NewProxy(options)
	proxy.Reload()

	httpListener, err := net.Listen("tcp", httpAddr)
	if err != nil {
		return err
	}
	httpsListener, err := net.Listen("tcp", httpsAddr)
	if err != nil {
		return err
	}
	errorLog := slog.NewLogLogger(slog.Default().Handler(), slog.LevelDebug)
	httpServer := &http.Server{
		Handler:           proxy.HTTPHandler(),
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
		ErrorLog:          errorLog,
	}
	// No read/write timeouts: uploads, downloads, streams and WebSockets may last long.
	httpsServer := &http.Server{
		Handler:           proxy.HTTPSHandler(),
		TLSConfig:         proxy.TLSConfig(),
		ReadHeaderTimeout: 30 * time.Second,
		IdleTimeout:       120 * time.Second,
		ErrorLog:          errorLog,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer stop()
	reload := make(chan os.Signal, 1)
	signal.Notify(reload, syscall.SIGHUP)
	go proxy.Watch(ctx, reload)

	errs := make(chan error, 2)
	go func() { errs <- httpServer.Serve(httpListener) }()
	go func() { errs <- httpsServer.ServeTLS(httpsListener, "", "") }()
	proxy.StartServing()
	slog.Info("proxy started", "version", version, "http", httpAddr, "https", httpsAddr, "acme", options.ACMECache != "")

	select {
	case err = <-errs:
	case <-ctx.Done():
		slog.Info("shutting down")
	}
	shutdownCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	shutdownErr := errors.Join(httpServer.Shutdown(shutdownCtx), httpsServer.Shutdown(shutdownCtx))
	if err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return shutdownErr
}
