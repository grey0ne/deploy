import json
import os
import re
import warnings
from dataclasses import dataclass, field, replace
from typing import Literal

from scripts.constants import PROJECT_DIR, project_env
from scripts.printing import print_error

DEPLOY_CONFIG_FILE = os.path.join(PROJECT_DIR, 'deploy.json')

StackType = Literal['django_nextjs', 'fastapi']

DEFAULT_SERVICES = {
    'celery': False,
    'centrifugo': False,
}

DEFAULT_DEV = {
    'postgres': True,
    'minio': True,
}

DEFAULT_DEV_FASTAPI = {
    'postgres': False,
    'minio': False,
}


class DeployConfigError(Exception):
    """deploy.json is missing, invalid, or incomplete."""


DEFAULT_FASTAPI_BACKEND_PATHS = ('api', 'ws', 'health', 'docs', 'openapi.json')

_PACKAGE_RE = re.compile(r'^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$')
_APP_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.]*:[A-Za-z_][A-Za-z0-9_]*$')
_PATH_SEGMENT_RE = re.compile(r'^[A-Za-z0-9_.-]+$')
_APT_PACKAGE_RE = re.compile(r'^[a-z0-9][a-z0-9+.-]*$')


@dataclass(frozen=True)
class FastapiConfig:
    """``fastapi`` section of deploy.json (fastapi stack only)."""

    # Directories (relative to the project root) with a pyproject.toml, installed into the image.
    packages: tuple[str, ...] = ()
    # uvicorn import string, e.g. "main:app" or "proxy.main:create_app".
    app: str = ''
    # Package directory used as uvicorn --app-dir and working dir; defaults to the last package.
    app_dir: str = ''
    factory: bool = False
    # Serve spa/ (Next.js) next to FastAPI; nginx sends backend_paths to FastAPI, the rest to Next.js.
    frontend: bool = False
    backend_paths: tuple[str, ...] = DEFAULT_FASTAPI_BACKEND_PATHS
    # Mount the Docker socket into the FastAPI container (apps that start containers).
    docker_socket: bool = False
    # Debian packages installed into the FastAPI image.
    apt_packages: tuple[str, ...] = ()
    # Shell command run in the FastAPI container by `dc migrate` and on production deploy.
    migrate_command: str = ''

    @property
    def effective_app_dir(self) -> str:
        return self.app_dir or (self.packages[-1] if self.packages else '')


@dataclass
class DeployConfig:
    stack: StackType = 'django_nextjs'
    celery_enabled: bool = False
    centrifugo_enabled: bool = False
    postgres_enabled: bool = True
    minio_enabled: bool = True
    django_worker_count: int = 2
    fastapi_worker_count: int = 2
    fastapi: FastapiConfig = field(default_factory=FastapiConfig)

    @property
    def is_fastapi(self) -> bool:
        return self.stack == 'fastapi'

    @property
    def has_nextjs(self) -> bool:
        """Next.js (spa/) is part of the stack: always for django_nextjs, optional for fastapi."""
        return not self.is_fastapi or self.fastapi.frontend

    @property
    def is_django_nextjs(self) -> bool:
        return self.stack == 'django_nextjs'

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


def _parse_stack(value) -> StackType:
    if value == 'fastapi':
        return 'fastapi'
    if value in (None, 'django_nextjs'):
        return 'django_nextjs'
    raise DeployConfigError(
        f'deploy.json field "stack" must be "django_nextjs" or "fastapi", got: {value!r}'
    )


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


def _str_list(section: dict, key: str, pattern: re.Pattern[str], default: tuple[str, ...]) -> tuple[str, ...]:
    value = section.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise DeployConfigError(f'deploy.json field "fastapi.{key}" must be a list of strings')
    for item in value:
        # Paths stay inside the project: no "." / ".." segments.
        if not pattern.match(item) or any(part in ('.', '..') for part in item.split('/')):
            raise DeployConfigError(f'deploy.json field "fastapi.{key}" has an invalid entry: {item!r}')
    return tuple(value)


def _parse_fastapi_section(section: dict) -> FastapiConfig:
    if not isinstance(section, dict):
        raise DeployConfigError('deploy.json field "fastapi" must be an object')
    packages = _str_list(section, 'packages', _PACKAGE_RE, ())
    app = section.get('app', '')
    if not isinstance(app, str) or not _APP_RE.match(app):
        raise DeployConfigError(
            'deploy.json field "fastapi.app" is required for the fastapi stack: '
            'a uvicorn import string like "main:app"'
        )
    if not packages:
        raise DeployConfigError(
            'deploy.json field "fastapi.packages" is required for the fastapi stack: '
            'directories with a pyproject.toml, e.g. ["backend"]'
        )
    app_dir = section.get('app_dir', '')
    if not isinstance(app_dir, str) or (app_dir and app_dir not in packages):
        raise DeployConfigError('deploy.json field "fastapi.app_dir" must be one of "fastapi.packages"')
    migrate_command = section.get('migrate_command', '')
    if not isinstance(migrate_command, str):
        raise DeployConfigError('deploy.json field "fastapi.migrate_command" must be a string')
    return FastapiConfig(
        packages=packages,
        app=app,
        app_dir=app_dir,
        factory=_parse_bool(section.get('factory'), False),
        frontend=_parse_bool(section.get('frontend'), False),
        backend_paths=_str_list(section, 'backend_paths', _PATH_SEGMENT_RE, DEFAULT_FASTAPI_BACKEND_PATHS),
        docker_socket=_parse_bool(section.get('docker_socket'), False),
        apt_packages=_str_list(section, 'apt_packages', _APT_PACKAGE_RE, ()),
        migrate_command=migrate_command.strip(),
    )


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


def load_deploy_config() -> DeployConfig:
    global _deploy_config
    if _deploy_config is not None:
        return _deploy_config

    django_worker_count = 2
    fastapi_worker_count = 2
    try:
        django_worker_count = int(os.getenv('DJANGO_WORKER_COUNT', '2'))
    except ValueError:
        django_worker_count = 2
    try:
        fastapi_worker_count = int(os.getenv('FASTAPI_WORKER_COUNT', '2'))
    except ValueError:
        fastapi_worker_count = 2

    data = _read_deploy_json()

    stack = _parse_stack(data.get('stack', 'django_nextjs'))

    django_section = data.get('django', {})
    if 'worker_count' in django_section:
        django_worker_count = int(django_section['worker_count'])

    fastapi_section = data.get('fastapi', {})
    if 'worker_count' in fastapi_section:
        fastapi_worker_count = int(fastapi_section['worker_count'])

    dev_defaults = DEFAULT_DEV_FASTAPI if stack == 'fastapi' else DEFAULT_DEV
    fastapi = _parse_fastapi_section(fastapi_section) if stack == 'fastapi' else FastapiConfig()

    config = DeployConfig(
        stack=stack,
        celery_enabled=_service_enabled(data, 'celery', DEFAULT_SERVICES['celery']),
        centrifugo_enabled=_service_enabled(data, 'centrifugo', DEFAULT_SERVICES['centrifugo']),
        postgres_enabled=_dev_enabled(data, 'postgres', dev_defaults['postgres']),
        minio_enabled=_dev_enabled(data, 'minio', dev_defaults['minio']),
        django_worker_count=django_worker_count,
        fastapi_worker_count=fastapi_worker_count,
        fastapi=fastapi,
    )
    _deploy_config = _apply_compose_profiles_fallback(config)
    return _deploy_config


def get_deploy_config() -> DeployConfig:
    return load_deploy_config()


def reset_deploy_config() -> None:
    global _deploy_config
    _deploy_config = None


def report_deploy_config_error(exc: DeployConfigError) -> None:
    print_error(str(exc))
