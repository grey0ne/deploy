# proxy

Reverse proxy in front of the projects, dev and production: TLS termination, routing by domain
and path prefix to upstream containers, WebSocket upgrades, streamed responses (server-sent
events), gzip of uncompressed text responses, request body limits, one access log line per
request. Go, two dependencies (`golang.org/x/crypto/acme/autocert`, `go.yaml.in/yaml/v3`).

## Sites

Every `*.yaml` / `*.yml` file of the config directory (hidden files are skipped, so a file can
be written as `.name.tmp` and renamed into place) lists sites; deploy writes one file per
project (`scripts/proxy/configuration.py`):

```yaml
sites:
  - domains: [example.com, www.example.com]
    tls:
      acme: true                  # Let's Encrypt; or cert: /path.crt and key: /path.key
    max_body_size: 50000k         # nginx syntax, k/m/g = 1024^n; default 1m
    compress: true                # default
    routes:
      - paths: [/api, /admin]     # whole segments: /api, /api/x — not /apix
        upstream: http://project-django:8000
        timeout: 120s             # wait for the response headers; default 60s
      - paths: [/]                # catch-all
        upstream: http://project-nextjs:3000
```

- The longest matching path wins; a path outside every route is 404. Paths are matched after
  cleaning (`//`, `..`) like nginx locations; the upstream gets the original request.
- The upstream gets the original `Host` and `X-Forwarded-For` / `X-Real-IP` (the client address),
  `X-Forwarded-Proto` / `X-Scheme` (`https`), `X-Forwarded-Host`. Such headers sent by the client
  are dropped.
- Port 80 answers ACME http-01 challenges and redirects the known domains to https (301, 308 for
  methods with a body). An unknown domain fails the TLS handshake (no certificate) or gets 421/404.
- Unknown fields are errors.

## Reloads

The config directory and the certificate files are checked every `-poll` (2s) and on `SIGHUP`;
changes are applied without a restart, requests in flight keep the routing table they started
with. Files are loaded independently: a file that does not parse, references a certificate that
does not load or claims a domain of an earlier file (name order) keeps its previously accepted
version (or is not served) and is logged; the other files are not affected. A certificate file
pair that fails to load (rewritten halfway) keeps the previous certificate and is retried.

Let's Encrypt certificates are requested as soon as a domain appears in the config (the result
is logged) and renewed by autocert 30 days before they expire, in the running process. Accounts
and certificates live in `-acme-cache`.

## Flags

| Flag | Default | |
|---|---|---|
| `-config` | `/config` | directory with the site files |
| `-http`, `-https` | `:80`, `:443` | listeners |
| `-poll` | `2s` | check interval of the config and certificate files |
| `-acme-cache` | empty | ACME cache directory; empty disables ACME |
| `-acme-email` | empty | Let's Encrypt account contact |
| `-acme-directory` | Let's Encrypt production | e.g. the staging directory for tests |
| `-check` | | validate the config directory strictly (any rejected file is an error) and exit |
| `-version` | | print the version |

## Development

```bash
cd deploy/proxy && go test -race ./...
```

The Dockerfile runs the tests before building. After a change bump `PROXY_VERSION` in
`scripts/compose/services.py` (and `version` in `main.go`): `dc up` builds a missing dev tag;
`deployprod` builds the production image locally (cross-compiled for `linux/amd64`), pushes it
with the project images and restarts the balancer when it runs another version.
