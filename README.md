# django-deploy

Scripts for automating deploy of django + nextjs or fastapi projects.

This repo is expected to be a **git submodule** at `deploy/` in the project repo. The project root should contain `deploy/`, `environment/`, and **`deploy.json`**. Django stacks also need `backend/` and `spa/`; FastAPI stacks use the repo root as the Python package context.

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
  "services": {
    "celery": { "enabled": false },
    "centrifugo": { "enabled": false }
  },
  "dev": {
    "postgres": { "enabled": false },
    "minio": { "enabled": false }
  },
  "fastapi": {
    "worker_count": 2
  }
}
```

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
python3 deploy/launch_command.py generate_compose  # with env.base + env.dev loaded
```

Production `prod.yml` is generated during `deployprod` and copied to the server (ephemeral local copy under `deploy/compose/`).

## Project layout

- **django_nextjs**: backend dependencies via `install-dependencies.sh` in `backend/`.
- **fastapi**: `proxy/pyproject.toml` and `shared/pyproject.toml`; images install those packages via `Dockerfile.fastapidev` / `Dockerfile.fastapiprod`.
