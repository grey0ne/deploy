# django-deploy

Shared deploy/dev tooling for Django + Next.js projects. Used as a git submodule at `<project>/deploy` by several projects — keep everything project-agnostic; project specifics come from env vars.

## Expected host project layout

```
<project>/
  backend/      Django project (pyproject.toml, install-dependencies.sh for apt deps)
  spa/          Next.js app
  deploy/       this repo
  environment/  env.base (committed), env.dev, env.prod (secrets), version
```

Scripts resolve paths relative to this layout (`scripts/constants.py`) and must be run from the project root.

## Contents

- `dc` — main CLI (bash). Sources `environment/env.base` + `env.dev` (+ optional `.env`). Simple commands are inline; `PY_COMMANDS` are dispatched to `launch_command.py` → `scripts/commands_list.py`. Unknown args fall through to `docker-compose` with the dev compose file.
- `pc` — wrapper meant to be copied to `/usr/local/bin/pc` (runs `./deploy/dc`).
- `docker/` — `Dockerfile.djangodev`, `Dockerfile.djangoprod`, `Dockerfile.nextjsdev`, `Dockerfile.nextjsprod`, `Dockerfile.devcontainer`; `Dockerfile.playwright` at the root.
- `compose/dev.yml` — dev stack (django, nextjs, postgres, minio, celery, centrifugo, redis; optional services via `COMPOSE_PROFILES`). `balancer.yml` / `prod_balancer.yml` — shared nginx balancer.
- `scripts/` — Python deploy tooling (stdlib only, run with host `python3.14`):
  - `constants.py` — `Environment` dataclass built from env vars (`project_env`)
  - `deploy_production.py` — bump version → build images (`--platform linux/amd64`) → push to registry → git commit version + Sentry release → collectstatic to S3 → render prod compose → copy to server → migrate → `docker stack` update → nginx reload
  - `docker_compose.py` — renders the prod swarm compose file (service image tags are hardcoded here)
  - `nginx/` — nginx config templates for dev and prod
  - `do_init.py` — DigitalOcean droplet / managed Postgres provisioning
  - `s3_*.py` — S3 backup/restore/bucket helpers
- `prod-scripts/` — files copied to the prod server (certbot renew cron).

## Image versions

Image tags are pinned in several places — when bumping, update all of them:
- `docker/Dockerfile.*`, `Dockerfile.playwright` (`FROM` + playwright npm version)
- `compose/dev.yml`, `compose/balancer.yml`, `compose/prod_balancer.yml`
- `scripts/docker_compose.py` (prod centrifugo / redis)
- `dc` (postgres image used for dump/restore)

The Python version in `Dockerfile.djangodev`, `Dockerfile.djangoprod` and `Dockerfile.devcontainer` must satisfy the host projects' `pyproject.toml`. Postgres major version must match the prod managed DB (`PG_VERSION` in `scripts/constants.py`). Changes here affect every project that uses this submodule.

## Dev environment notes

- Dev compose bind-mounts `<project>/spa` → `/app` (nextjs) and `<project>/backend` → `/app/src` (django). `node_modules` therefore lives on the host but must be installed from inside the Linux container (`dc rebuild` does this).
- Containers are named `${PROJECT_NAME}-<service>` and join the `${PROJECT_NAME}` network plus the shared `devnet` used by the balancer.
