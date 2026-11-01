# django-deploy

Shared deploy/dev tooling for Django + Next.js (`django_nextjs` stack) and FastAPI (`fastapi` stack) projects. Used as a git submodule at `<project>/deploy` by several projects — keep everything project-agnostic; project specifics come from env vars.

## Expected host project layout

```
<project>/
  backend/      Django project (pyproject.toml, install-dependencies.sh for apt deps)
  spa/          Next.js app
  deploy/       this repo
  deploy.json   required: stack type, optional services, worker counts (see README.md, schema in deploy.schema.json)
  environment/  env.base (committed), env.dev, env.prod (secrets), version
```

FastAPI projects list their Python package directories in `deploy.json` (`fastapi.packages`, built with the repo root as context) and may add `spa/` with `fastapi.frontend`.

Scripts resolve paths relative to this layout (`scripts/constants.py`) and must be run from the project root.

## Contents

- `dc` — main CLI (bash). Sources `environment/env.base` + `env.dev` (+ optional `.env`), regenerates compose files on every run, then runs the command. Simple commands are inline; `PY_COMMANDS` are dispatched to `launch_command.py` → `scripts/commands_list.py` (with `PYTHONPATH=deploy`). Unknown args fall through to `docker-compose` with the generated dev compose file. Host Python is invoked as `python3.15` (macOS system `python3` is too old).
- `pc` — wrapper meant to be copied to `/usr/local/bin/pc` (runs `./deploy/dc`).
- `docker/` — `Dockerfile.django{dev,prod}`, `Dockerfile.nextjs{dev,prod}`, `Dockerfile.devcontainer`; `Dockerfile.playwright` at the root. FastAPI Dockerfiles are generated from `deploy.json` by `scripts/compose/fastapi_image.py` into `compose/generated/`.
- `compose/generated/` — dev and balancer compose files, generated from `deploy.json` (gitignored, never edit). `COMPOSE_PROFILES` is deprecated in favour of `deploy.json` `services.*.enabled`.
- `scripts/` — Python deploy tooling (stdlib only, run with host `python3.15`):
  - `constants.py` — `Environment` dataclass built from env vars (`project_env`)
  - `deploy_config.py` — loads and validates `deploy.json` (`get_deploy_config()`)
  - `compose/` — compose generation: `services.py` (service definitions + image constants), `fastapi_image.py` (FastAPI Dockerfiles + uvicorn command), `renderer.py`, `generate.py`, `yaml_dump.py`
  - `deploy_production.py` — check registry config → issue missing SSL certs (same as `certprod`) → bump version → build images (`--platform linux/amd64`) → push to registry → git commit version + Sentry release → collectstatic to S3 → render prod compose → copy to server → migrate → `docker stack` update → nginx reload
  - `docker_compose.py` — writes the prod swarm compose file (via `compose/renderer.py`)
  - `nginx/` — nginx config templates for dev and prod
  - `do_init.py` — DigitalOcean droplet / managed Postgres provisioning
  - `s3_*.py` — S3 backup/restore/bucket helpers
- `prod-scripts/` — files copied to the prod server (certbot renew cron).

## Image versions

Image tags are pinned in several places — when bumping, update all of them:
- `docker/Dockerfile.*`, `Dockerfile.playwright` (`FROM` + playwright npm version)
- `scripts/compose/fastapi_image.py` — `PYTHON_IMAGE` for the generated FastAPI Dockerfiles
- `scripts/compose/services.py` — `*_IMAGE` constants (postgres, redis, centrifugo, nginx, minio) used for dev, balancer and prod
- `dc` (postgres image used for dump/restore)

The Python version in the django/fastapi Dockerfiles and `Dockerfile.devcontainer` must satisfy the host projects' `pyproject.toml`. Postgres major version must match the prod managed DB (`PG_VERSION` in `scripts/constants.py`). Changes here affect every project that uses this submodule.

## Dev environment notes

- Dev compose bind-mounts `<project>/spa` → `/app` (nextjs) and `<project>/backend` → `/app/src` (django). `node_modules` therefore lives on the host but must be installed from inside the Linux container (`dc rebuild` does this).
- Containers are named `${PROJECT_NAME}-<service>` and join the `${PROJECT_NAME}` network plus the shared `devnet` used by the balancer.
