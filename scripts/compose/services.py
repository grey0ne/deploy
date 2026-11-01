import os

from scripts.constants import (
    COMPOSE_DIR,
    DJANGO_DATA_DIR,
    DJANGO_DIR,
    FASTAPI_BUILD_CONTEXT,
    NEXTJS_DIR,
    PROJECT_DIR,
    project_env,
)
from scripts.compose.fastapi_image import DEV_DOCKERFILE, FASTAPI_PORT, uvicorn_command
from scripts.deploy_config import BackendName, DeployConfig

DJANGO_PORT = 8000

GENERATED_DIR = os.path.join(COMPOSE_DIR, 'generated')
DOCKER_SOCKET = '/var/run/docker.sock'

MINIO_IMAGE = 'minio/minio:RELEASE.2025-09-07T16-13-09Z'
POSTGRES_IMAGE = 'postgres:16.15-alpine'
REDIS_IMAGE = 'redis:8.10.3-alpine'
CENTRIFUGO_IMAGE = 'centrifugo/centrifugo:v6.10.2'
NGINX_IMAGE = 'nginx:1.30.6-alpine-slim'


def _env_files_dev() -> list[str]:
    return [
        f'{PROJECT_DIR}/environment/env.base',
        f'{PROJECT_DIR}/environment/env.dev',
    ]


def _env_files_prod() -> list[str]:
    name = project_env.project_name
    return [
        f'/app/{name}/env.base',
        f'/app/{name}/env',
    ]


def dev_django_service() -> dict:
    name = project_env.project_name
    service: dict = {
        'domainname': f'{name}-django',
        'image': f'{name}-django',
        'container_name': f'{name}-django',
        'build': {
            'context': DJANGO_DIR,
            'dockerfile': f'{PROJECT_DIR}/deploy/docker/Dockerfile.djangodev',
        },
        'env_file': _env_files_dev(),
        'volumes': [
            f'{DJANGO_DIR}:/app/src',
            f'{DJANGO_DATA_DIR}:/app/backend_data',
        ],
        'networks': ['app', 'devnet'],
    }
    return service


def dev_fastapi_service(config: DeployConfig) -> dict:
    name = project_env.project_name
    fastapi = config.fastapi
    volumes = [f'{PROJECT_DIR}/{package}:/deps/{package}' for package in fastapi.packages]
    if fastapi.docker_socket:
        volumes.append(f'{DOCKER_SOCKET}:{DOCKER_SOCKET}')
    return {
        'domainname': f'{name}-fastapi',
        'image': f'{name}-fastapi',
        'container_name': f'{name}-fastapi',
        'build': {
            'context': FASTAPI_BUILD_CONTEXT,
            'dockerfile': DEV_DOCKERFILE,
        },
        'env_file': _env_files_dev(),
        'volumes': volumes,
        # Services on the host (e.g. a local LLM server); built in on Docker Desktop, needed on Linux.
        'extra_hosts': ['host.docker.internal:host-gateway'],
        'networks': ['app', 'devnet'],
    }


def backend_internal_url(backend: BackendName) -> str:
    name = project_env.project_name
    port = FASTAPI_PORT if backend == 'fastapi' else DJANGO_PORT
    return f'http://{name}-{backend}:{port}'


def _nextjs_backend_environment(config: DeployConfig) -> dict:
    """Backend URL for server-side requests from Next.js; the frontend itself is backend-agnostic."""
    assert config.nextjs.backend is not None
    return {'BACKEND_INTERNAL_URL': backend_internal_url(config.nextjs.backend)}


def dev_nextjs_service(config: DeployConfig) -> dict:
    name = project_env.project_name
    service: dict = {
        'image': f'{name}-nextjs',
        'container_name': f'{name}-nextjs',
        'domainname': f'{name}-nextjs',
        'build': {
            'context': NEXTJS_DIR,
            'dockerfile': f'{PROJECT_DIR}/deploy/docker/Dockerfile.nextjsdev',
        },
        'env_file': _env_files_dev(),
        'volumes': [f'{NEXTJS_DIR}:/app'],
        'networks': ['app', 'devnet'],
    }
    service['environment'] = _nextjs_backend_environment(config)
    return service


def dev_postgres_service() -> dict:
    name = project_env.project_name
    return {
        'image': POSTGRES_IMAGE,
        'container_name': f'{name}-postgres',
        'domainname': f'{name}-postgres',
        'environment': {
            'POSTGRES_PASSWORD': name,
            'POSTGRES_USER': name,
            'POSTGRES_DB': name,
        },
        'volumes': ['postgres-data:/var/lib/postgresql/data'],
        'networks': ['app'],
    }


def dev_minio_service() -> dict:
    name = project_env.project_name
    return {
        'command': 'server /data --console-address ":9001"',
        'image': MINIO_IMAGE,
        'container_name': f'{name}-minio',
        'domainname': f'{name}-minio',
        'environment': {
            'MINIO_ROOT_USER': f'{name}_minio',
            'MINIO_ROOT_PASSWORD': f'{name}_minio',
        },
        'volumes': ['minio-data:/data'],
        'networks': ['app', 'devnet'],
    }


def dev_celery_service() -> dict:
    name = project_env.project_name
    return {
        'image': f'{name}-django',
        'domainname': f'{name}-celery',
        'container_name': f'{name}-celery',
        'command': 'celery --app application.celeryapp worker -E -l info',
        'volumes': [f'{DJANGO_DIR}:/app/src'],
        'env_file': _env_files_dev(),
        'networks': ['app', 'devnet'],
        'profiles': ['celery'],
    }


def dev_centrifugo_service() -> dict:
    name = project_env.project_name
    return {
        'image': CENTRIFUGO_IMAGE,
        'container_name': f'{name}-centrifugo',
        'domainname': f'{name}-centrifugo',
        'command': 'centrifugo',
        'env_file': _env_files_dev(),
        'networks': ['app', 'devnet'],
        'profiles': ['centrifugo'],
    }


def dev_redis_service() -> dict:
    name = project_env.project_name
    return {
        'image': REDIS_IMAGE,
        'container_name': f'{name}-redis',
        'domainname': f'{name}-redis',
        'volumes': ['redis-data:/data'],
        'networks': ['app', 'devnet'],
        'profiles': ['celery', 'centrifugo'],
    }


def prod_django_service(django_image: str, worker_count: int) -> dict:
    name = project_env.project_name
    return {
        f'{name}-django': {
            'image': django_image,
            'command': (
                f'gunicorn -w {worker_count} -b 0.0.0.0:8000 '
                '-k application.worker.CustomUvicornWorker application.asgi'
            ),
            'networks': ['prodnet'],
            'env_file': _env_files_prod(),
            'volumes': [f'/app/{name}/backend_data:/app/backend_data'],
        },
    }


def prod_fastapi_service(config: DeployConfig, fastapi_image: str) -> dict:
    name = project_env.project_name
    service: dict = {
        'image': fastapi_image,
        'command': ' '.join(
            uvicorn_command(config.fastapi, dev=False, workers=config.fastapi_worker_count)
        ),
        'networks': ['prodnet'],
        'env_file': _env_files_prod(),
    }
    if config.fastapi.docker_socket:
        service['volumes'] = [f'{DOCKER_SOCKET}:{DOCKER_SOCKET}']
    return {f'{name}-fastapi': service}


def prod_nextjs_service(config: DeployConfig, nextjs_image: str) -> dict:
    name = project_env.project_name
    service: dict = {
        'image': nextjs_image,
        'networks': ['prodnet'],
        'env_file': _env_files_prod(),
    }
    service['environment'] = _nextjs_backend_environment(config)
    return {f'{name}-nextjs': service}


def prod_celery_service(django_image: str) -> dict:
    name = project_env.project_name
    return {
        f'{name}-celery': {
            'image': django_image,
            'command': 'celery --app application.celeryapp worker -E -l info',
            'volumes': [f'/app/{name}/backend_data:/app/backend_data'],
            'env_file': _env_files_prod(),
            'networks': ['prodnet'],
        },
    }


def prod_centrifugo_service() -> dict:
    name = project_env.project_name
    return {
        f'{name}-centrifugo': {
            'image': CENTRIFUGO_IMAGE,
            'command': 'centrifugo',
            'env_file': _env_files_prod(),
            'depends_on': [f'{name}-redis'],
            'networks': ['prodnet'],
        },
    }


def prod_redis_service() -> dict:
    name = project_env.project_name
    return {
        f'{name}-redis': {
            'image': REDIS_IMAGE,
            'networks': ['prodnet'],
        },
    }


def _build_dev_optional_services(config: DeployConfig, services: dict) -> None:
    if config.dev_includes_optional('postgres'):
        services['postgres'] = dev_postgres_service()
        for backend in ('django', 'fastapi'):
            if backend in services:
                services[backend]['depends_on'] = ['postgres']

    if config.dev_includes_optional('minio'):
        services['minio'] = dev_minio_service()

    if config.dev_includes_optional('celery'):
        services['celery'] = dev_celery_service()

    if config.dev_includes_optional('centrifugo'):
        services['centrifugo'] = dev_centrifugo_service()

    if config.dev_includes_optional('redis'):
        services['redis'] = dev_redis_service()


def build_dev_compose(config: DeployConfig) -> dict:
    name = project_env.project_name

    services: dict = {}
    if config.django.enabled:
        services['django'] = dev_django_service()
    if config.fastapi.enabled:
        services['fastapi'] = dev_fastapi_service(config)
    if config.nextjs.enabled:
        services['nextjs'] = dev_nextjs_service(config)

    _build_dev_optional_services(config, services)

    volumes: dict = {}
    if config.postgres_enabled:
        volumes['postgres-data'] = {
            'driver': 'local',
            'name': f'{name}-postgres-data',
        }
    if config.minio_enabled:
        volumes['minio-data'] = {
            'driver': 'local',
            'name': f'{name}-minio-data',
        }
    if config.needs_redis():
        volumes['redis-data'] = {
            'driver': 'local',
            'name': f'{name}-redis-data',
        }

    return {
        'name': name,
        'services': services,
        'volumes': volumes,
        'networks': {
            'app': {'name': name},
            'devnet': {'name': 'devnet', 'external': True},
        },
    }


def build_prod_compose(
    config: DeployConfig,
    django_image: str = '',
    nextjs_image: str = '',
    fastapi_image: str = '',
) -> dict:
    services: dict = {}
    if config.django.enabled:
        services.update(prod_django_service(django_image, config.django_worker_count))
    if config.fastapi.enabled:
        services.update(prod_fastapi_service(config, fastapi_image))
    if config.nextjs.enabled:
        services.update(prod_nextjs_service(config, nextjs_image))

    if config.celery_enabled:
        services.update(prod_celery_service(django_image))
    if config.centrifugo_enabled:
        services.update(prod_centrifugo_service())
    if config.needs_redis():
        services.update(prod_redis_service())

    return {
        'services': services,
        'networks': {
            'prodnet': {'name': 'prodnet', 'external': True},
        },
    }


def build_dev_balancer_compose() -> dict:
    return {
        'name': 'balancer',
        'services': {
            'nginx': {
                'image': NGINX_IMAGE,
                'container_name': 'nginx',
                'environment': {
                    'NGINX_ENTRYPOINT_LOCAL_RESOLVERS': 'yes',
                },
                'volumes': [
                    '${NGINX_CONFIG_DIR}:/etc/nginx/templates',
                    '${SSL_CERTS_DIR}:/app/ssl',
                ],
                'ports': ['80:80', '443:443'],
                'networks': ['devnet'],
            },
        },
        'networks': {
            'devnet': {'name': 'devnet', 'external': True},
        },
    }


def build_prod_balancer_compose() -> dict:
    return {
        'services': {
            'nginx': {
                'image': NGINX_IMAGE,
                'volumes': [
                    '/app/certbot/certificates:/etc/letsencrypt',
                    '/app/certbot/challenge:/var/www/certbot',
                    '/app/balancer/conf:/etc/nginx/conf.d',
                ],
                'ports': ['80:80', '443:443'],
                'networks': ['prodnet'],
            },
        },
        'networks': {
            'prodnet': {'name': 'prodnet', 'external': True},
        },
    }
