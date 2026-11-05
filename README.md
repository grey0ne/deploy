# deploy

Dev and production tooling — docker compose, images, the shared nginx balancer, Docker Swarm
deploy — for projects built from independent components: a **Django** backend, a **FastAPI**
backend and a **Next.js** frontend, in any combination.

This repo is used as a git submodule at `deploy/` in each project. Everything here is
project-agnostic; a project describes itself in `deploy.json` and `environment/`.

## Setup

Prerequisites on the host: Docker, `python3.15` (`dc` and the scripts use only the standard library).

```bash
git submodule add git@github.com:grey0ne/deploy.git deploy
sudo cp deploy/pc /usr/local/bin/pc     # `pc <command>` = `./deploy/dc <command>` from the project root
```

Then add `deploy.json` and `environment/` (below) and run `pc up`. A complete walkthrough for a new
project, including production infrastructure and Sentry, is in [NEW_PROJECT.md](NEW_PROJECT.md).

**Renamed from `django-deploy`.** GitHub redirects the old URL, but update existing projects:

```bash
git config -f .gitmodules submodule.deploy.url git@github.com:grey0ne/deploy.git
git submodule sync deploy
```

## Project layout

```
<project>/
  deploy/            this repo (submodule)
  deploy.json        enabled components and services (required; schema: deploy/deploy.schema.json)
  environment/       env.base + version (committed), env.dev, env.prod (secrets, not committed)
  django_backend/    Django project: pyproject.toml, install-dependencies.sh       (if "django")
  fastapi_backend/   FastAPI app with a pyproject.toml (default fastapi.packages)  (if "fastapi")
  nextjs_frontend/   Next.js app                                                   (if "nextjs")
  backend_data/      Django runtime data in dev (name kept for existing deployments)
```

| File | Purpose |
|---|---|
| `environment/env.base` | Shared project settings: `PROJECT_NAME`, `PROJECT_DOMAIN`, registry, `SSL_CERTS_DIR`, `NGINX_CONFIG_DIR` |
| `environment/env.dev` | Development secrets and overrides |
| `environment/env.prod` | Production secrets |
| `environment/version` | Release version, bumped by `deployprod` |

## deploy.json

A component is enabled when its section is present (`"enabled": false` switches it off without
deleting it). Every step — dev compose, image builds, migrations, tests, nginx routing,
production deploy — runs for each enabled component on its own.

Django + Next.js:

```json
{
  "django": { "worker_count": 2 },
  "nextjs": {},
  "services": { "celery": { "enabled": false }, "centrifugo": { "enabled": false } },
  "dev": { "postgres": { "enabled": true }, "minio": { "enabled": true } }
}
```

FastAPI + Next.js:

```json
{
  "fastapi": {
    "app": "main:app",
    "paths": ["api", "health"],
    "migrate_command": "alembic -c alembic.ini upgrade head"
  },
  "nextjs": {},
  "dev": { "postgres": { "enabled": true } }
}
```

FastAPI only (all traffic goes to FastAPI), with a shared package installed first:

```json
{
  "fastapi": { "packages": ["shared", "fastapi_backend"], "app": "main:create_app", "factory": true }
}
```

Django + FastAPI + Next.js:

```json
{
  "django": { "paths": ["admin", "api", "static"] },
  "fastapi": { "app": "main:app", "paths": ["v1", "ws"] },
  "nextjs": { "backend": "django" },
  "default_backend": "django"
}
```

### Components

`django`:

| Field | Meaning |
|---|---|
| `worker_count` | gunicorn workers in production (falls back to `DJANGO_WORKER_COUNT`, then 2). |
| `paths` | First path segments nginx routes to Django; default `admin`, `api`, `idp`, `login`, `complete`, `static`, `s`. |

`fastapi`:

| Field | Meaning |
|---|---|
| `app` (required) | uvicorn import string, e.g. `main:app`. |
| `packages` | Directories with a `pyproject.toml`, installed in this order; default `["fastapi_backend"]`. Dev: `pip install -e "<pkg>[dev]"`, mounted into the container (so `dc test` finds tools from the `dev` extra); prod: `pip install <pkg>`. |
| `app_dir` | Package used as the working directory / import root; defaults to the last package. |
| `factory` | `app` is an application factory (`uvicorn --factory`). |
| `paths` | First path segments nginx routes to FastAPI (unbuffered for server-sent events, WebSocket upgrade); default `api`, `ws`, `health`, `docs`, `openapi.json`. |
| `worker_count` | uvicorn workers in production (falls back to `FASTAPI_WORKER_COUNT`, then 2). |
| `docker_socket` | Mount `/var/run/docker.sock` into the FastAPI container (dev and prod). |
| `apt_packages` | Debian packages installed into the FastAPI image. |
| `migrate_command` | Run by `dc migrate` and on `deployprod`. |

The FastAPI Dockerfiles are generated from these settings into
`deploy/compose/generated/Dockerfile.fastapi{dev,prod}`, with a matching `.dockerignore` that
sends only `packages` to the build.

`nextjs`:

| Field | Meaning |
|---|---|
| `backend` | Backend for server-side requests (`BACKEND_INTERNAL_URL`) and `dc generatetypes`; required when both backends are enabled, otherwise the only backend. |

Top level:

| Field | Meaning |
|---|---|
| `default_backend` | Serves extra domains and, without `nextjs`, all paths not routed to the other backend. Required when both backends are enabled. |
| `max_body_size` | nginx `client_max_body_size` of the application server (dev and prod), e.g. `"100m"`; default `"50000k"`. A larger request is answered 413 before it reaches the backend. |
| `services` | Optional production (and dev) services: `celery` (requires `django`), `centrifugo`; `redis` is added when either is enabled. |
| `dev` | Local-only services: `postgres`, `minio`. Both default to enabled when `django` is enabled, disabled otherwise. |

Invalid values, overlapping `django.paths` / `fastapi.paths`, a missing `nextjs.backend` /
`default_backend` or celery without Django stop every `dc` command with a readable message.

### Routing

- Each enabled backend gets an nginx location for its `paths`.
- `location /` goes to Next.js when it is enabled; otherwise to `default_backend` (a single backend gets all traffic).
- Extra domains (`EXTRA_DOMAINS`) send all traffic to `default_backend`.
- With Django, `media.<domain>` serves MinIO in dev.

### Frontend ↔ backend

The Next.js service gets `BACKEND_INTERNAL_URL` (`http://${PROJECT_NAME}-django:8000` or
`http://${PROJECT_NAME}-fastapi:8080`, per `nextjs.backend`) for server-side API requests. The
frontend (and `next_utils`) does not know which backend framework is used; compatibility is
defined by the HTTP API only (`/api/openapi.json`, auth cookies and endpoints).

## Commands

Run from the project root as `pc <command>` (or `./deploy/dc <command>`; `pc help` lists them).
Development commands load `env.base` + `env.dev` and regenerate the dev compose files first;
production commands load `env.base` + `env.prod` and never touch the dev files (`.env` is applied
last in both). Anything that is not a command below goes to `docker compose` with the dev compose
file (`pc logs -f fastapi`, `pc run --rm -T nextjs npm ci`, …).

Development:

| Command | What it does |
|---|---|
| `up` | Start the dev balancer and the project. First fixes only what is missing or outdated: dev certificates (generated and trusted; regenerated when they expire within 14 days or do not cover the domain), `/etc/hosts` entries, the `devnet` network and the project's nginx config (validated with `nginx -t` together with all other projects' configs before the balancer restarts). `sudo` is asked only when a certificate or hosts entry must be added. |
| `rebuild` | Rebuild images of all enabled components and restart. For Next.js it **deletes `package-lock.json`** and `node_modules` and runs `npm install`; to keep the lock file use `pc build <component>` and `pc run --rm -T nextjs npm ci`. |
| `migrate` | Django `migrate` and/or `fastapi.migrate_command`. |
| `test` | `pytest` in each enabled backend container. |
| `generatetypes` | OpenAPI types from `nextjs.backend` into `nextjs_frontend/api/apiTypes.ts`. |
| `sh <service>` | bash in a running container. |
| `tmux` | tmux session with the logs of the balancer and every component. |
| `nginx …` | `docker compose` for the shared balancer (`pc nginx logs -f nginx`). |
| `gencerts`, `updatenginx` | Regenerate all dev certificates (+ `/etc/hosts`); write and reload the balancer config. Usually not needed: `up` does both when required. |
| `django …`, `format`, `devsuperuser <name>` | Django only: `manage.py`, isort + black, a superuser with password `devpass`. |
| `updatesubmodules` | `git submodule update --init --recursive --remote`. |

Data:

| Command | What it does |
|---|---|
| `dumpdev`, `restoredev` | Dev Postgres to / from `backup/<project>dev.sql`. |
| `dumpprod`, `restoreprod <db> <file>` | Production Postgres to `backup/<project>.sql` / from a file. |
| `s3backupdev`, `s3restoredev`, `s3backupprod`, `s3restoreprod`, `s3createdevbucket` | S3 files to / from a local directory; create the dev bucket. |

Production:

| Command | What it does |
|---|---|
| `deployprod` | Issue missing SSL certificates, bump the version, build and push images of enabled components (`linux/amd64`), render the Swarm compose, migrate each backend, update the stack, reload nginx. |
| `certprod` | Issue missing SSL certificates for the production domains. |
| `initprod`, `deploybalancer` | Provision DigitalOcean infrastructure and the production balancer. |
| `djangoprod …`, `fastapiprod …` | One-off command in the production image (`manage.py …` / the given command). |
| `logsprod <service>` | `docker service logs` of a production service. |

## Compose generation

Compose files are generated under `deploy/compose/generated/` (gitignored, never edited) from
`deploy.json`; development commands do it before they run. By hand:

```bash
./deploy/dc generate_compose
```

The production `prod.yml` is generated during `deployprod` and copied to the server.

## Upgrading from the `stack` layout

Older projects had `"stack": "django_nextjs"` / `"fastapi"` and `backend/` / `spa/`
directories; such a deploy.json is rejected with a pointer here. Update the `deploy` and
`next_utils` submodules together: `next_utils` requires `BACKEND_INTERNAL_URL`, which only the
component-based deploy provides.

Django + Next.js (`"stack": "django_nextjs"`):

1. `git mv backend django_backend`, `git mv spa nextjs_frontend`. Submodules inside them move
   too (`django_backend/django_utils`, `nextjs_frontend/next_utils`); check `.gitmodules` and
   run `git submodule sync`.
2. deploy.json: remove `"stack"`, keep `"django"` and add `"nextjs": {}`. Custom Django URL
   prefixes outside the defaults go to `django.paths`.
3. Update paths in CI workflows, `.gitignore`, `.dockerignore`, editor settings and scripts.
4. Next.js: `next_utils` reads the backend URL from `BACKEND_INTERNAL_URL` only;
   `NavigationHeader` has no `showDjangoAdmin` — add `{ url: '/admin/', external: true, needAdmin: true }` to `links`.
5. `pc rebuild`, `pc updatenginx`, `pc up`; check the site, then deploy as usual.

FastAPI (`"stack": "fastapi"`):

1. Move the package to `fastapi_backend/`, or list the existing directories in `fastapi.packages`.
   A frontend moves to `nextjs_frontend/`.
2. deploy.json: remove `"stack"`; rename `backend_paths` → `paths`; replace `"frontend": true`
   with a top-level `"nextjs": {}` (FastAPI-only projects need no `nextjs` section).
3. `pc rebuild`, `pc updatenginx`, `pc up`.

## Development of this repo

`dc` is a Python script (`scripts/cli.py`): each command is a `Command` entry with its handler,
environment (`dev` / `prod`), whether it needs the dev compose files and a required component.
Tests: `python3.15 -m unittest discover deploy/tests` from a project root.
