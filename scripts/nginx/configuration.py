"""nginx configs composed from the enabled components.

Routing: each enabled backend gets a location for its ``paths``; ``location /`` goes to
Next.js when it is enabled, otherwise to ``default_backend`` (whose paths are then covered
by the catch-all). Extra domains send everything to ``default_backend``.
"""
import re

from scripts.constants import project_env
from scripts.deploy_config import BackendName, DeployConfig, get_deploy_config
from scripts.nginx.templates import (
    APP_SERVER_TEMPLATE,
    CENTRIFUGO_DEV_TEMPLATE,
    CENTRIFUGO_PROD_TEMPLATE,
    DJANGO_LOCATION_TEMPLATE,
    FASTAPI_LOCATION_TEMPLATE,
    HTTP_UPGRADE_TEMPLATE,
    NEXTJS_LOCATION_TEMPLATE,
    NGINX_BASE_REDIRECT_NO_MEDIA_TEMPLATE,
    NGINX_BASE_REDIRECT_TEMPLATE,
    NGINX_DEV_MEDIA_TEMPLATE,
    PROD_HTTP_TEMPLATE,
)
from scripts.printing import print_status

_DEV_RESOLVER = '$NGINX_LOCAL_RESOLVERS'
_PROD_RESOLVER = '127.0.0.11'

_LOCATION_TEMPLATES = {
    'django': DJANGO_LOCATION_TEMPLATE,
    'fastapi': FASTAPI_LOCATION_TEMPLATE,
    'nextjs': NEXTJS_LOCATION_TEMPLATE,
}


def custom_format(template: str, **kwargs: str) -> str:
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def _location(component: str, match: str) -> str:
    return custom_format(
        _LOCATION_TEMPLATES[component], MATCH=match, PROJECT_NAME=project_env.project_name
    )


def _paths_match(paths: tuple[str, ...]) -> str:
    return f"~ ^/({'|'.join(re.escape(p) for p in paths)})(/|$)"


def _backend_paths(config: DeployConfig, backend: BackendName) -> tuple[str, ...]:
    return config.django.paths if backend == 'django' else config.fastapi.paths


def app_locations(config: DeployConfig) -> str:
    """Locations of the main domain."""
    catch_all = 'nextjs' if config.nextjs.enabled else config.default_backend
    assert catch_all is not None
    blocks = [
        _location(backend, _paths_match(_backend_paths(config, backend)))
        for backend in config.backends
        if backend != catch_all
    ]
    blocks.append(_location(catch_all, '/'))
    return ''.join(blocks)


def extra_domain_locations(config: DeployConfig) -> str:
    assert config.default_backend is not None
    return _location(config.default_backend, '/')


def _server(*, domain: str, cert: str, key: str, resolver: str, locations: str) -> str:
    return custom_format(
        APP_SERVER_TEMPLATE,
        MAX_BODY_SIZE=get_deploy_config().max_body_size,
        DOMAIN=domain,
        SSL_CERTIFICATE=cert,
        SSL_CERTIFICATE_KEY=key,
        RESOLVER=resolver,
        LOCATIONS=locations.rstrip('\n'),
    )


def _dev_server(domain: str, cert_name: str, locations: str) -> str:
    return _server(
        domain=domain,
        cert=f'/app/ssl/{cert_name}.crt',
        key=f'/app/ssl/{cert_name}.key',
        resolver=_DEV_RESOLVER,
        locations=locations,
    )


def _prod_server(domain: str, locations: str) -> str:
    # $connection_upgrade has no shared definition in production; repeating the map is valid.
    return HTTP_UPGRADE_TEMPLATE + custom_format(PROD_HTTP_TEMPLATE, DOMAIN=domain) + _server(
        domain=domain,
        cert=f'/etc/letsencrypt/live/{domain}/fullchain.pem',
        key=f'/etc/letsencrypt/live/{domain}/privkey.pem',
        resolver=_PROD_RESOLVER,
        locations=locations,
    )


def _write(target_file: str, content: str) -> None:
    with open(target_file, 'w') as f:
        f.write(content)


def render_dev_nginx_conf(target_file: str):
    print_status(f"Generating dev nginx config to {target_file}")
    config = get_deploy_config()
    domain = project_env.project_domain
    name = project_env.project_name
    extra_domains_str = ' '.join(project_env.extra_domains)
    # The media domain belongs to Django (MinIO behind media.<domain>).
    redirect = NGINX_BASE_REDIRECT_TEMPLATE if config.django.enabled else NGINX_BASE_REDIRECT_NO_MEDIA_TEMPLATE
    parts = [
        custom_format(redirect, PROJECT_DOMAIN=domain, EXTRA_DOMAINS=extra_domains_str),
        HTTP_UPGRADE_TEMPLATE,
    ]
    if config.django.enabled:
        parts.append(custom_format(NGINX_DEV_MEDIA_TEMPLATE, PROJECT_NAME=name, PROJECT_DOMAIN=domain))
    parts.append(_dev_server(domain, name, app_locations(config)))
    _write(target_file, '\n'.join(parts))


def render_extra_dev_domain_nginx_conf(target_file: str, domain: str):
    print_status(f"Generating extra domain {domain} nginx config to {target_file}")
    config = get_deploy_config()
    cert_name = f'{project_env.project_name}_{domain}'
    _write(target_file, _dev_server(domain, cert_name, extra_domain_locations(config)))


def render_centrifugo_dev_nginx_conf(target_file: str):
    print_status(f"Generating dev centrifugo nginx config to {target_file}")
    conf_str = custom_format(
        CENTRIFUGO_DEV_TEMPLATE,
        PROJECT_NAME=project_env.project_name,
        PROJECT_DOMAIN=project_env.project_domain,
    )
    _write(target_file, conf_str)


def render_app_prod_nginx_conf(target_file: str):
    print_status(f"Generating prod nginx config to {target_file}")
    config = get_deploy_config()
    _write(target_file, _prod_server(project_env.project_domain, app_locations(config)))


def render_centrifugo_prod_nginx_conf(target_file: str):
    print_status(f"Generating prod centrifugo nginx config to {target_file}")
    conf_str = custom_format(
        CENTRIFUGO_PROD_TEMPLATE,
        PROJECT_NAME=project_env.project_name,
        PROJECT_DOMAIN=project_env.project_domain,
    )
    _write(target_file, conf_str)


def render_extra_domain_prod_nginx_conf(target_file: str, domain: str):
    print_status(f"Generating extra domain {domain} prod nginx config to {target_file}")
    config = get_deploy_config()
    _write(target_file, _prod_server(domain, extra_domain_locations(config)))
