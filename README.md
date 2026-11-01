# django-deploy

Scripts for automating deploy of django + nextjs or fastapi projects.

This repo is expected to be a **git submodule** at `deploy/` in the project repo. The project root should contain `deploy/`, `environment/`, and **`deploy.json`**. Django stacks also need `backend/` and `spa/`; FastAPI stacks list their Python package directories in `deploy.json` and may add `spa/` (Next.js). Host scripts run with `python3.15`.

## Configuration

| Location | Purpose |
|----------|---------|
| `deploy.json` (project root) | Non-secret deploy toggles: optional services, gunicorn workers, dev-only services |
| `environment/env.base` | Shared project settings (`PROJECT_NAME`, registry, domains) |
| `environment/env.dev` | Development secrets and overrides |
| `environment/env.prod` | Production secrets |

### deploy.json — django + nextjs (default stack)

```json
{
  "stack": "django_nextjs",
  "services": {
    "celery": { "enabled": false },
    "centrifugo": { "enabled": false }
  },
  "dev": {
    "postgres": { "enabled": true },
    "minio": { "enabled": true }
  },
  "django": {
    "worker_count": 2
  }
}
```

### deploy.json — fastapi + nginx balancer

```json
{
  "stack": "fastapi",
  "dev": {
    "postgres": { "enabled": true }
  },
  "fastapi": {
    "worker_count": 1,
    "packages": ["backend"],
    "app": "main:app",
    "frontend": true,
    "backend_paths": ["api", "v1", "health"],
    "docker_socket": true,
    "apt_packages": ["docker-cli"],
    "migrate_command": "alembic -c alembic.ini upgrade head"
  }
}
```

`fastapi` fields:

| Field | Meaning |
|---|---|
| `packages` (required) | Directories with a `pyproject.toml`, installed into the image in this order (`pip install -e` in dev, mounted into the container). |
| `app` (required) | uvicorn import string, e.g. `main:app` or `proxy.main:create_app`. |
| `app_dir` | Package used as the working directory / import root; defaults to the last package. |
| `factory` | `app` is an application factory (`uvicorn --factory`). |
| `frontend` | Add the Next.js service from `spa/`; nginx routes `backend_paths` to FastAPI (unbuffered, WebSocket upgrade) and everything else to Next.js. Without it all traffic goes to FastAPI. |
| `backend_paths` | First path segments routed to FastAPI when `frontend` is on; default `api`, `ws`, `health`, `docs`, `openapi.json`. |
| `docker_socket` | Mount `/var/run/docker.sock` into the FastAPI container (dev and prod). |
| `apt_packages` | Debian packages installed into the FastAPI image. |
| `migrate_command` | Run by `dc migrate` and on `deployprod`. |

The FastAPI Dockerfiles are generated from these settings into `deploy/compose/generated/Dockerfile.fastapi{dev,prod}` (with a matching `.dockerignore` that sends only `packages` to the build). Migrating an existing FastAPI project (e.g. llm_proxy): add `"packages": ["shared", "proxy"], "app": "proxy.main:create_app", "factory": true`.

- **stack**: `django_nextjs` (default) or `fastapi` (single backend behind nginx).
- **services**: optional production (and dev) stack pieces. `redis` is added automatically when celery or centrifugo is enabled.
- **dev**: local-only services (`postgres`, `minio`). Defaults to disabled for `fastapi` stack.
- **django.worker_count**: gunicorn workers in production (falls back to `DJANGO_WORKER_COUNT`).
- **fastapi.worker_count**: uvicorn workers in production (falls back to `FASTAPI_WORKER_COUNT`).

**`deploy.json` is required** in the project root. Deploy scripts exit with a readable error if it is missing or contains invalid JSON.

`COMPOSE_PROFILES` is deprecated; use `deploy.json` `services.*.enabled` instead.

## Docker Compose generation

Compose files are generated under `deploy/compose/generated/` (gitignored). Regeneration runs automatically when using `./deploy/dc` or `pc`.

Manual generation:

```bash
./deploy/dc generate_compose
# or
python3.15 deploy/launch_command.py generate_compose  # with env.base + env.dev loaded
```

Production `prod.yml` is generated during `deployprod` and copied to the server (ephemeral local copy under `deploy/compose/`).

## Frontend ↔ backend

The Next.js service gets `BACKEND_INTERNAL_URL` (`http://${PROJECT_NAME}-django:8000` or `http://${PROJECT_NAME}-fastapi:8080`) for server-side API requests. The frontend (and `next_utils`) does not know which backend framework is used; compatibility is defined by the HTTP API only.

## Project layout

- **django_nextjs**: backend dependencies via `install-dependencies.sh` in `backend/`.
- **fastapi**: the `fastapi.packages` directories (each with a `pyproject.toml`); `spa/` when `fastapi.frontend` is enabled. `dc migrate` runs `fastapi.migrate_command`; `dc generatetypes` reads `/api/openapi.json` from FastAPI.
