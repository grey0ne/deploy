# django-deploy

Dev and production tooling (docker compose, images, nginx balancer, Docker Swarm deploy) for projects built from independent components: a **Django** backend, a **FastAPI** backend and a **Next.js** frontend, in any combination.

This repo is a **git submodule** at `deploy/` in the project repo. Host scripts run with `python3.15`; use them through `./deploy/dc` (or the `pc` shortcut) from the project root.

## Project layout

```
<project>/
  deploy/            this repo (submodule)
  deploy.json        enabled components and services (required)
  environment/       env.base + version (committed), env.dev, env.prod (secrets)
  django_backend/    Django project: pyproject.toml, install-dependencies.sh       (if "django")
  fastapi_backend/   FastAPI app with a pyproject.toml (default fastapi.packages)  (if "fastapi")
  nextjs_frontend/   Next.js app                                                   (if "nextjs")
  backend_data/      Django runtime data in dev (name kept for existing deployments)
```

| Location | Purpose |
|----------|---------|
| `deploy.json` | Components, routing, optional services, worker counts |
| `environment/env.base` | Shared project settings (`PROJECT_NAME`, `PROJECT_DOMAIN`, registry, `SSL_CERTS_DIR`, `NGINX_CONFIG_DIR`) |
| `environment/env.dev` | Development secrets and overrides |
| `environment/env.prod` | Production secrets |

## deploy.json

A component is enabled when its section is present (set `"enabled": false` to switch it off without deleting it). Every step — dev compose, image builds, migrations, tests, nginx routing, production deploy — runs for each enabled component independently.

Django + Next.js:

```json
{
  "django": { "worker_count": 2 },
  "nextjs": {},
  "services": {
    "celery": { "enabled": false },
    "centrifugo": { "enabled": false }
  },
  "dev": {
    "postgres": { "enabled": true },
    "minio": { "enabled": true }
  }
}
```

FastAPI + Next.js:

```json
{
  "fastapi": {
    "worker_count": 1,
    "app": "main:app",
    "paths": ["api", "v1", "health"],
    "docker_socket": true,
    "apt_packages": ["docker-cli"],
    "migrate_command": "alembic -c alembic.ini upgrade head"
  },
  "nextjs": {},
  "dev": { "postgres": { "enabled": true } }
}
```

FastAPI only (all traffic to FastAPI):

```json
{
  "fastapi": { "packages": ["shared", "proxy"], "app": "proxy.main:create_app", "factory": true }
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
| `app` (required) | uvicorn import string, e.g. `main:app` or `proxy.main:create_app`. |
| `packages` | Directories with a `pyproject.toml`, installed in this order; default `["fastapi_backend"]`. Dev: `pip install -e "<pkg>[dev]"` and mounted into the container (so `dc test` finds tools from the `dev` extra); prod: `pip install <pkg>`. |
| `app_dir` | Package used as the working directory / import root; defaults to the last package. |
| `factory` | `app` is an application factory (`uvicorn --factory`). |
| `paths` | First path segments nginx routes to FastAPI (unbuffered for server-sent events, WebSocket upgrade); default `api`, `ws`, `health`, `docs`, `openapi.json`. |
| `worker_count` | uvicorn workers in production (falls back to `FASTAPI_WORKER_COUNT`, then 2). |
| `docker_socket` | Mount `/var/run/docker.sock` into the FastAPI container (dev and prod). |
| `apt_packages` | Debian packages installed into the FastAPI image. |
| `migrate_command` | Run by `dc migrate` and on `deployprod`. |

The FastAPI Dockerfiles are generated from these settings into `deploy/compose/generated/Dockerfile.fastapi{dev,prod}`, with a matching `.dockerignore` that sends only `packages` to the build.

`nextjs`:

| Field | Meaning |
|---|---|
| `backend` | Backend for server-side requests (`BACKEND_INTERNAL_URL`) and `dc generatetypes`; required when both backends are enabled, otherwise the only backend. |

Top level:

| Field | Meaning |
|---|---|
| `default_backend` | Serves extra domains and, without `nextjs`, all paths not routed to the other backend. Required when both backends are enabled. |
| `services` | Optional production (and dev) services: `celery` (requires `django`), `centrifugo`; `redis` is added when either is enabled. |
| `dev` | Local-only services: `postgres`, `minio`. Both default to enabled when `django` is enabled, disabled otherwise. |

Validation errors (invalid values, overlapping `django.paths` / `fastapi.paths`, missing `nextjs.backend` / `default_backend`, celery without Django) stop every `dc` command with a readable message. `COMPOSE_PROFILES` is deprecated; use `services.*.enabled`.

### Routing

- Each enabled backend gets an nginx location for its `paths`.
- `location /` goes to Next.js when it is enabled; otherwise to `default_backend` (with a single backend, that backend gets all traffic).
- Extra domains (`EXTRA_DOMAINS`) send all traffic to `default_backend`.
- With Django, `media.<domain>` serves MinIO in dev.

## Frontend ↔ backend

The Next.js service gets `BACKEND_INTERNAL_URL` (`http://${PROJECT_NAME}-django:8000` or `http://${PROJECT_NAME}-fastapi:8080`, per `nextjs.backend`) for server-side API requests. The frontend (and `next_utils`) does not know which backend framework is used; compatibility is defined by the HTTP API only (`/api/openapi.json`, auth cookies and endpoints).

## Commands

| Command | What it does |
|---|---|
| `dc up` | Start the dev balancer and the project (`dc` passes unknown arguments to `docker-compose`). |
| `dc rebuild` | Rebuild images of all enabled components; reinstall `node_modules` for Next.js. |
| `dc migrate` | Django `migrate` and/or `fastapi.migrate_command`, for each enabled backend. |
| `dc test` | `pytest` in each enabled backend container. |
| `dc django …`, `dc format`, `dc devsuperuser …` | Django only. |
| `dc generatetypes` | OpenAPI types from `nextjs.backend` into `nextjs_frontend/api/apiTypes.ts`. |
| `dc gencerts`, `dc updatenginx` | Dev certificates (+ `/etc/hosts`) and balancer config. |
| `dc deployprod` | Build and push images of enabled components, migrate each backend, update the Swarm stack and nginx. |

## Docker Compose generation

Compose files are generated under `deploy/compose/generated/` (gitignored). Regeneration runs automatically when using `./deploy/dc` or `pc`.

```bash
./deploy/dc generate_compose
# or
python3.15 deploy/launch_command.py generate_compose  # with env.base + env.dev loaded
```

Production `prod.yml` is generated during `deployprod` and copied to the server (ephemeral local copy under `deploy/compose/`).

## Migrating to components

The `stack` field (`django_nextjs` / `fastapi`) and the `backend/` / `spa/` directories are gone; deploy.json with `stack` is rejected with a pointer here. Update the `deploy` and `next_utils` submodules together: the new `next_utils` requires `BACKEND_INTERNAL_URL`, which only the new deploy provides.

### Django + Next.js projects (`"stack": "django_nextjs"`)

1. Move the code:
   ```bash
   git mv backend django_backend
   git mv spa nextjs_frontend
   ```
   Submodules inside them move with `git mv` (`backend/django_utils` → `django_backend/django_utils`, `spa/next_utils` → `nextjs_frontend/next_utils`); check `.gitmodules` afterwards and run `git submodule sync`.
2. deploy.json: remove `"stack"`, keep the `"django"` section (`worker_count`) and add `"nextjs": {}`:
   ```json
   { "django": { "worker_count": 2 }, "nextjs": {}, "services": { … }, "dev": { … } }
   ```
   Custom Django URL prefixes outside the defaults go to `django.paths`.
3. Update paths that point at the old directories: CI workflows, `.gitignore`, `.dockerignore`, editor/devcontainer settings, scripts (`backend/…` → `django_backend/…`, `spa/…` → `nextjs_frontend/…`). `backend_data/` keeps its name.
4. Next.js: `next_utils` reads the backend URL from `BACKEND_INTERNAL_URL` only (set by deploy); `NavigationHeader` no longer has `showDjangoAdmin` — add an admin link to `links` (`{ url: '/admin/', external: true, needAdmin: true }`).
5. `dc rebuild`, `dc updatenginx`, `dc up`; check the site, then deploy as usual.

### FastAPI projects (`"stack": "fastapi"`)

1. Move the FastAPI package to `fastapi_backend/` (default `fastapi.packages`), or keep the existing directories and list them in `fastapi.packages` (e.g. llm_proxy: `["shared", "proxy"]`). A frontend moves to `nextjs_frontend/`.
2. deploy.json: remove `"stack"`; the `"fastapi"` section stays. Rename `backend_paths` → `paths`; replace `"frontend": true` with a top-level `"nextjs": {}`. FastAPI-only projects need no `nextjs` section: all traffic goes to FastAPI.
3. llm_proxy (pre-generalization deploy.json): `"fastapi": { "packages": ["shared", "proxy"], "app": "proxy.main:create_app", "factory": true, "worker_count": 2 }`.
4. `dc rebuild`, `dc updatenginx`, `dc up`.
