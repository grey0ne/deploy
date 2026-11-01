# deploy

Shared deploy/dev tooling (`github.com/grey0ne/deploy`, formerly `django-deploy`) for projects built from independent components — Django backend, FastAPI backend, Next.js frontend — in any combination. Used as a git submodule at `<project>/deploy` by several projects — keep everything project-agnostic; project specifics come from `deploy.json` and env vars.

## Expected host project layout

```
<project>/
  django_backend/   Django project (pyproject.toml, install-dependencies.sh for apt deps)   if "django" in deploy.json
  fastapi_backend/  FastAPI app with pyproject.toml (default fastapi.packages)              if "fastapi"
  nextjs_frontend/  Next.js app                                                             if "nextjs"
  deploy/           this repo
  deploy.json       required: enabled components, routing paths, optional services (see README.md, schema in deploy.schema.json)
  environment/      env.base (committed), env.dev, env.prod (secrets), version
```

Never branch on "the stack": every step checks each component on its own (`config.django.enabled`, `config.fastapi.enabled`, `config.nextjs.enabled`, `config.components`, `config.backends`). Paths come from `scripts/constants.py` (`DJANGO_DIR`, `NEXTJS_DIR`, `FASTAPI_BUILD_CONTEXT`).

Scripts resolve paths relative to this layout and must be run from the project root.

## Contents

- `dc` — main CLI: a `python3.15` script (macOS system `python3` is too old) that calls `scripts/cli.py`. Every command is one `Command` in `cli.COMMANDS` with its handler (`module:function`), the environment it loads (`dev`: env.base + env.dev, `prod`: env.base + env.prod, each + optional `.env`; `None`), whether it uses the dev compose files (regenerated first — production commands never do) and a required component. Unknown args fall through to `docker compose` with the generated dev compose file. Add a command there, never in shell scripts.
- `pc` — wrapper meant to be copied to `/usr/local/bin/pc` (runs `./deploy/dc`).
- `docker/` — `Dockerfile.django{dev,prod}`, `Dockerfile.nextjs{dev,prod}`, `Dockerfile.devcontainer`. FastAPI Dockerfiles are generated from `deploy.json` by `scripts/compose/fastapi_image.py` into `compose/generated/`.
- `compose/generated/` — dev and balancer compose files, generated from `deploy.json` (gitignored, never edit). `COMPOSE_PROFILES` is deprecated in favour of `deploy.json` `services.*.enabled`.
- `scripts/` — Python deploy tooling (stdlib only, run with host `python3.15`):
  - `cli.py` — command registry, dispatch and error reporting (no traceback for config/env errors, exit code of a failed subprocess)
  - `env_files.py` — parses and loads the env files once per run (quotes, `export`, `$VAR`; `~` is expanded in `constants.py`)
  - `paths.py` — project layout paths (importable before the environment is loaded); `errors.py` — `EnvConfigError`
  - `constants.py` — `Environment` dataclass built from env vars on import (`project_env`), so modules needing it are imported by `cli` after the env files are loaded
  - `dev_commands.py` — dev commands over `docker compose` (project and balancer), tmux, dev database dump/restore
  - `prod_tools.py` — one-off production commands over ssh (database dump/restore, commands in the app images, logs)
  - `commands.py` — production balancer/certificates setup, image builds, S3 backup commands
  - `deploy_config.py` — loads and validates `deploy.json` into per-component configs (`get_deploy_config()`)
  - `dev_balancer.py` — dev certificates, `/etc/hosts`, project nginx config for the shared balancer; `ensure_dev_balancer` (run by `dc up`) repairs only what is missing or outdated (certificates expiring within `CERT_RENEW_BEFORE_DAYS` or not covering the domain are regenerated, stale keychain copies removed, the balancer restarted) and validates a changed config with `nginx -t` before the balancer reloads
  - `compose/` — compose generation: `services.py` (service definitions + image constants), `fastapi_image.py` (FastAPI Dockerfiles + uvicorn command), `renderer.py`, `generate.py`, `yaml_dump.py`
  - `deploy_production.py` — check registry config → issue missing SSL certs (same as `certprod`) → bump version → build images of enabled components (`--platform linux/amd64`) → push to registry → git commit version + Sentry release → collectstatic to S3 (Django) → render prod compose → copy to server → migrate each backend → `docker stack` update → nginx reload
  - `docker_compose.py` — writes the prod swarm compose file (via `compose/renderer.py`)
  - `nginx/` — nginx configs composed from per-component location blocks (dev and prod)
  - `do_init.py` — DigitalOcean droplet / managed Postgres provisioning
  - `s3_*.py` — S3 backup/restore/bucket helpers
- `prod-scripts/` — files copied to the prod server (certbot renew cron).
- `tests/` — `python3.15 -m unittest discover deploy/tests` from the project root (stdlib only).

## Image versions

Image tags are pinned in several places — when bumping, update all of them:
- `docker/Dockerfile.*` (`FROM`)
- `scripts/compose/fastapi_image.py` — `PYTHON_IMAGE` for the generated FastAPI Dockerfiles
- `scripts/compose/services.py` — `*_IMAGE` constants (postgres, redis, centrifugo, nginx, minio) used for dev, balancer and prod
- `dc` (postgres image used for dump/restore)

The Python version in the django/fastapi Dockerfiles and `Dockerfile.devcontainer` must satisfy the host projects' `pyproject.toml`. Postgres major version must match the prod managed DB (`PG_VERSION` in `scripts/constants.py`). Changes here affect every project that uses this submodule.

## Dev environment notes

- Dev compose bind-mounts `<project>/nextjs_frontend` → `/app` (nextjs), `<project>/django_backend` → `/app/src` (django) and each `fastapi.packages` dir → `/deps/<pkg>` (fastapi). `node_modules` therefore lives on the host but must be installed from inside the Linux container (`dc rebuild` does this).
- Containers are named `${PROJECT_NAME}-<service>` and join the `${PROJECT_NAME}` network plus the shared `devnet` used by the balancer.
