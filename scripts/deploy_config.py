import json
import os
import warnings
from dataclasses import dataclass
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


@dataclass
class DeployConfig:
    stack: StackType = 'django_nextjs'
    celery_enabled: bool = False
    centrifugo_enabled: bool = False
    postgres_enabled: bool = True
    minio_enabled: bool = True
    django_worker_count: int = 2
    fastapi_worker_count: int = 2

    @property
    def is_fastapi(self) -> bool:
        return self.stack == 'fastapi'

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
    celery = config.celery_enabled or 'celery' in profiles
    centrifugo = config.centrifugo_enabled or 'centrifugo' in profiles
    return DeployConfig(
        stack=config.stack,
        celery_enabled=celery,
        centrifugo_enabled=centrifugo,
        postgres_enabled=config.postgres_enabled,
        minio_enabled=config.minio_enabled,
        django_worker_count=config.django_worker_count,
        fastapi_worker_count=config.fastapi_worker_count,
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

    config = DeployConfig(
        stack=stack,
        celery_enabled=_service_enabled(data, 'celery', DEFAULT_SERVICES['celery']),
        centrifugo_enabled=_service_enabled(data, 'centrifugo', DEFAULT_SERVICES['centrifugo']),
        postgres_enabled=_dev_enabled(data, 'postgres', dev_defaults['postgres']),
        minio_enabled=_dev_enabled(data, 'minio', dev_defaults['minio']),
        django_worker_count=django_worker_count,
        fastapi_worker_count=fastapi_worker_count,
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
