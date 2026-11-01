"""deploy.json: independent components (django, fastapi, nextjs) and optional services.

Components are enabled by their section being present (unless ``"enabled": false``). Code
lives in fixed directories next to deploy/: django_backend/, fastapi_backend/ (default for
``fastapi.packages``), nextjs_frontend/.
"""
import json
import os
import re
import warnings
from dataclasses import dataclass, field, replace
from typing import Literal

from scripts.constants import PROJECT_DIR, project_env
from scripts.printing import print_error

DEPLOY_CONFIG_FILE = os.path.join(PROJECT_DIR, 'deploy.json')

BackendName = Literal['django', 'fastapi']
BACKENDS: tuple[BackendName, ...] = ('django', 'fastapi')

DEFAULT_SERVICES = {
    'celery': False,
    'centrifugo': False,
}

DEFAULT_DJANGO_PATHS = ('admin', 'api', 'idp', 'login', 'complete', 'static', 's')
DEFAULT_FASTAPI_PATHS = ('api', 'ws', 'health', 'docs', 'openapi.json')
DEFAULT_FASTAPI_PACKAGES = ('fastapi_backend',)

MIGRATION_HINT = 'See "Migrating to components" in deploy/README.md.'

_PACKAGE_RE = re.compile(r'^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$')
_APP_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$')
_PATH_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_.-]+$')
_APT_PACKAGE_RE = re.compile(r'^[a-z0-9][a-z0-9+.-]*$')


class DeployConfigError(Exception):
    """deploy.json is missing, invalid, or incomplete."""


@dataclass(frozen=True)
class DjangoConfig:
    enabled: bool = False
    worker_count: int = 2
    # First path segments nginx routes to Django.
    paths: tuple[str, ...] = DEFAULT_DJANGO_PATHS


@dataclass(frozen=True)
class FastapiConfig:
    enabled: bool = False
    worker_count: int = 2
    # Directories (relative to the project root) with a pyproject.toml, installed into the image.
    packages: tuple[str, ...] = DEFAULT_FASTAPI_PACKAGES
    # uvicorn import string, e.g. "main:app" or "proxy.main:create_app".
    app: str = ''
    # Package directory used as uvicorn import root and working dir; defaults to the last package.
    app_dir: str = ''
    factory: bool = False
    # First path segments nginx routes to FastAPI (unbuffered, WebSocket upgrade).
    paths: tuple[str, ...] = DEFAULT_FASTAPI_PATHS
    # Mount the Docker socket into the FastAPI container (apps that start containers).
    docker_socket: bool = False
    # Debian packages installed into the FastAPI image.
    apt_packages: tuple[str, ...] = ()
    # Shell command run in the FastAPI container by `dc migrate` and on production deploy.
    migrate_command: str = ''

    @property
    def effective_app_dir(self) -> str:
        return self.app_dir or self.packages[-1]


@dataclass(frozen=True)
class NextjsConfig:
    enabled: bool = False
    # Backend for server-side requests from Next.js (BACKEND_INTERNAL_URL).
    backend: BackendName | None = None


@dataclass
class DeployConfig:
    django: DjangoConfig = field(default_factory=DjangoConfig)
    fastapi: FastapiConfig = field(default_factory=FastapiConfig)
    nextjs: NextjsConfig = field(default_factory=NextjsConfig)
    # Receives all traffic not matched by backend paths when Next.js is disabled
    # (and all traffic of extra domains).
    default_backend: BackendName | None = None
    celery_enabled: bool = False
    centrifugo_enabled: bool = False
    postgres_enabled: bool = True
    minio_enabled: bool = True

    @property
    def backends(self) -> tuple[BackendName, ...]:
        return tuple(b for b in BACKENDS if self.backend_enabled(b))

    def backend_enabled(self, backend: BackendName) -> bool:
        return self.django.enabled if backend == 'django' else self.fastapi.enabled

    @property
    def django_worker_count(self) -> int:
        return self.django.worker_count

    @property
    def fastapi_worker_count(self) -> int:
        return self.fastapi.worker_count

    @property
    def components(self) -> tuple[str, ...]:
        """Enabled application services, as named in the dev compose file."""
        return self.backends + (('nextjs',) if self.nextjs.enabled else ())

    def is_enabled(self, service: str) -> bool:
        return {
            'celery': self.celery_enabled,
            'centrifugo': self.centrifugo_enabled,
            'postgres': self.postgres_enabled,
            'minio': self.minio_enabled,
            'redis': self.needs_redis(),
        }.get(service, False)

    def needs_redis(self) -> bool:
        return self.celery_enabled or self.centrifugo_enabled

    def dev_includes_optional(self, service: str) -> bool:
        if service == 'redis':
            return self.needs_redis()
        return self.is_enabled(service)


_deploy_config: DeployConfig | None = None


def _parse_bool(value, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.lower() in ('true', '1', 'yes')
    return default


def _service_enabled(data: dict, name: str, default: bool) -> bool:
    services = data.get('services', {})
    entry = services.get(name, {})
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        return _parse_bool(entry.get('enabled'), default)
    return default


def _dev_enabled(data: dict, name: str, default: bool) -> bool:
    dev = data.get('dev', {})
    entry = dev.get(name, {})
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        return _parse_bool(entry.get('enabled'), default)
    return default


def _section(data: dict, name: str) -> dict | None:
    """Component section, or None when absent / disabled."""
    if name not in data:
        return None
    section = data[name]
    if section is True:
        return {}
    if section is False or section is None:
        return None
    if not isinstance(section, dict):
        raise DeployConfigError(f'deploy.json field "{name}" must be an object')
    if not _parse_bool(section.get('enabled'), True):
        return None
    return section


def _str_list(
    section: dict, prefix: str, key: str, pattern: re.Pattern[str], default: tuple[str, ...]
) -> tuple[str, ...]:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DeployConfigError(f'deploy.json field "{prefix}.{key}" must be a list of strings')
    for item in value:
        # Paths stay inside the project: no "." / ".." segments.
        if not pattern.match(item) or any(part in ('.', '..') for part in item.split('/')):
            raise DeployConfigError(f'deploy.json field "{prefix}.{key}" has an invalid entry: {item!r}')
    return tuple(value)


def _worker_count(section: dict, prefix: str, env_var: str) -> int:
    value = section.get('worker_count')
    if value is None:
        try:
            return int(os.getenv(env_var, '2'))
        except ValueError:
            return 2
    if not isinstance(value, int) or value < 1:
        raise DeployConfigError(f'deploy.json field "{prefix}.worker_count" must be a positive integer')
    return value


def _parse_django(data: dict) -> DjangoConfig:
    section = _section(data, 'django')
    if section is None:
        return DjangoConfig()
    return DjangoConfig(
        enabled=True,
        worker_count=_worker_count(section, 'django', 'DJANGO_WORKER_COUNT'),
        paths=_str_list(section, 'django', 'paths', _PATH_SEGMENT_RE, DEFAULT_DJANGO_PATHS),
    )


def _parse_fastapi(data: dict) -> FastapiConfig:
    section = _section(data, 'fastapi')
    if section is None:
        return FastapiConfig()
    packages = _str_list(section, 'fastapi', 'packages', _PACKAGE_RE, DEFAULT_FASTAPI_PACKAGES)
    if not packages:
        raise DeployConfigError('deploy.json field "fastapi.packages" must not be empty')
    app = section.get('app', '')
    if not isinstance(app, str) or not _APP_RE.match(app):
        raise DeployConfigError(
            'deploy.json field "fastapi.app" is required: a uvicorn import string like "main:app"'
        )
    app_dir = section.get('app_dir', '')
    if not isinstance(app_dir, str) or (app_dir and app_dir not in packages):
        raise DeployConfigError('deploy.json field "fastapi.app_dir" must be one of "fastapi.packages"')
    migrate_command = section.get('migrate_command', '')
    if not isinstance(migrate_command, str):
        raise DeployConfigError('deploy.json field "fastapi.migrate_command" must be a string')
    return FastapiConfig(
        enabled=True,
        worker_count=_worker_count(section, 'fastapi', 'FASTAPI_WORKER_COUNT'),
        packages=packages,
        app=app,
        app_dir=app_dir,
        factory=_parse_bool(section.get('factory'), False),
        paths=_str_list(section, 'fastapi', 'paths', _PATH_SEGMENT_RE, DEFAULT_FASTAPI_PATHS),
        docker_socket=_parse_bool(section.get('docker_socket'), False),
        apt_packages=_str_list(section, 'fastapi', 'apt_packages', _APT_PACKAGE_RE, ()),
        migrate_command=migrate_command.strip(),
    )


def _parse_backend_name(value, field_name: str, backends: tuple[BackendName, ...]) -> BackendName | None:
    if value is None:
        return None
    if value not in backends:
        raise DeployConfigError(
            f'deploy.json field "{field_name}" must be an enabled backend '
            f'({", ".join(backends) or "none enabled"}), got: {value!r}'
        )
    return value


def _validate_components(config: DeployConfig) -> None:
    backends = config.backends
    if not backends and not config.nextjs.enabled:
        raise DeployConfigError(
            'deploy.json enables no components: add a "django", "fastapi" and/or "nextjs" section. '
            + MIGRATION_HINT
        )
    if config.nextjs.enabled and not backends:
        raise DeployConfigError('deploy.json "nextjs" needs a backend: enable "django" or "fastapi"')
    if len(backends) == 2:
        overlap = sorted(set(config.django.paths) & set(config.fastapi.paths))
        if overlap:
            raise DeployConfigError(
                'deploy.json "django.paths" and "fastapi.paths" overlap '
                f'({", ".join(overlap)}): set the paths so each prefix goes to one backend'
            )
        if config.nextjs.enabled and config.nextjs.backend is None:
            raise DeployConfigError(
                'deploy.json "nextjs.backend" is required when both django and fastapi are enabled'
            )
        if config.default_backend is None:
            raise DeployConfigError(
                'deploy.json "default_backend" is required when both django and fastapi are enabled '
                '(it serves extra domains and, without nextjs, unmatched paths)'
            )
    if (config.celery_enabled) and not config.django.enabled:
        raise DeployConfigError('deploy.json "services.celery" requires the "django" component')


def _read_deploy_json() -> dict:
    if not os.path.isfile(DEPLOY_CONFIG_FILE):
        raise DeployConfigError(
            'deploy.json is required but was not found.\n'
            f'  Expected path: {DEPLOY_CONFIG_FILE}\n'
            '  Create deploy.json in the project root. See deploy/README.md for examples.'
        )
    try:
        with open(DEPLOY_CONFIG_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise DeployConfigError(
            f'deploy.json contains invalid JSON ({DEPLOY_CONFIG_FILE}):\n'
            f'  {exc}'
        ) from exc
    if not isinstance(data, dict):
        raise DeployConfigError(
            f'deploy.json must be a JSON object, got {type(data).__name__}.'
        )
    return data


def _apply_compose_profiles_fallback(config: DeployConfig) -> DeployConfig:
    profiles = project_env.compose_profiles
    if not profiles:
        return config
    warnings.warn(
        'COMPOSE_PROFILES is deprecated; use deploy.json services.*.enabled instead.',
        DeprecationWarning,
        stacklevel=2,
    )
    return replace(
        config,
        celery_enabled=config.celery_enabled or 'celery' in profiles,
        centrifugo_enabled=config.centrifugo_enabled or 'centrifugo' in profiles,
    )


def load_deploy_config() -> DeployConfig:
    global _deploy_config
    if _deploy_config is not None:
        return _deploy_config

    data = _read_deploy_json()
    if 'stack' in data:
        raise DeployConfigError(
            'deploy.json field "stack" is no longer supported: components are configured '
            'independently ("django", "fastapi", "nextjs" sections) and code moved to '
            'django_backend/, fastapi_backend/ and nextjs_frontend/. ' + MIGRATION_HINT
        )

    django = _parse_django(data)
    fastapi = _parse_fastapi(data)
    backends = tuple(b for b, c in (('django', django), ('fastapi', fastapi)) if c.enabled)

    nextjs = NextjsConfig()
    nextjs_section = _section(data, 'nextjs')
    if nextjs_section is not None:
        backend = _parse_backend_name(nextjs_section.get('backend'), 'nextjs.backend', backends)
        if backend is None and len(backends) == 1:
            backend = backends[0]
        nextjs = NextjsConfig(enabled=True, backend=backend)

    default_backend = _parse_backend_name(data.get('default_backend'), 'default_backend', backends)
    if default_backend is None and len(backends) == 1:
        default_backend = backends[0]

    # Local postgres/minio default to the Django setup; FastAPI-only projects opt in.
    config = DeployConfig(
        django=django,
        fastapi=fastapi,
        nextjs=nextjs,
        default_backend=default_backend,
        celery_enabled=_service_enabled(data, 'celery', DEFAULT_SERVICES['celery']),
        centrifugo_enabled=_service_enabled(data, 'centrifugo', DEFAULT_SERVICES['centrifugo']),
        postgres_enabled=_dev_enabled(data, 'postgres', django.enabled),
        minio_enabled=_dev_enabled(data, 'minio', django.enabled),
    )
    config = _apply_compose_profiles_fallback(config)
    _validate_components(config)
    _deploy_config = config
    return _deploy_config


def get_deploy_config() -> DeployConfig:
    return load_deploy_config()


def reset_deploy_config() -> None:
    global _deploy_config
    _deploy_config = None


def report_deploy_config_error(exc: DeployConfigError) -> None:
    print_error(str(exc))
