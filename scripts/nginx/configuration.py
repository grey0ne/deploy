import re

from scripts.constants import project_env
from scripts.deploy_config import get_deploy_config
from scripts.nginx.templates import (
    NGINX_BASE_REDIRECT_TEMPLATE,
    NGINX_BASE_REDIRECT_FASTAPI_TEMPLATE,
    HTTP_UPGRADE_TEMPLATE,
    NGINX_DEV_MEDIA_TEMPLATE,
    NGINX_DEV_APP_TEMPLATE,
    CENTRIFUGO_DEV_TEMPLATE,
    NGINX_EXTRA_DEV_DOMAINS_TEMPLATE,
    NGINX_EXTRA_DEV_DOMAINS_FASTAPI_TEMPLATE,
    APP_PROD_TEMPLATE,
    FASTAPI_DEV_APP_TEMPLATE,
    FASTAPI_PROD_APP_TEMPLATE,
    CENTRIFUGO_PROD_TEMPLATE,
    EXTRA_DOMAIN_PROD_TEMPLATE,
    EXTRA_DOMAIN_PROD_FASTAPI_TEMPLATE,
    FASTAPI_NEXTJS_SERVER_TEMPLATE,
    FASTAPI_NEXTJS_PROD_HTTP_TEMPLATE,
)
from scripts.printing import print_status


def custom_format(template: str, **kwargs: str) -> str:
    for key, value in kwargs.items():
        template = template.replace(f"{{{key}}}", value)
    return template


def _fastapi_nextjs_server(*, domain: str, cert: str, key: str, resolver: str) -> str:
    config = get_deploy_config()
    return custom_format(
        FASTAPI_NEXTJS_SERVER_TEMPLATE,
        DOMAIN=domain,
        SSL_CERTIFICATE=cert,
        SSL_CERTIFICATE_KEY=key,
        RESOLVER=resolver,
        BACKEND_PATHS='|'.join(re.escape(p) for p in config.fastapi.backend_paths),
        PROJECT_NAME=project_env.project_name,
    )


def _fastapi_nextjs_dev_server(domain: str, cert_name: str) -> str:
    return _fastapi_nextjs_server(
        domain=domain,
        cert=f'/app/ssl/{cert_name}.crt',
        key=f'/app/ssl/{cert_name}.key',
        resolver='$NGINX_LOCAL_RESOLVERS',
    )


def _with_upgrade_map(conf: str) -> str:
    """FastAPI configs use $connection_upgrade; production has no shared definition of the map
    (only the centrifugo config declares it). Repeating the map in several files is valid nginx."""
    return HTTP_UPGRADE_TEMPLATE + conf


def _fastapi_nextjs_prod_server(domain: str) -> str:
    return custom_format(FASTAPI_NEXTJS_PROD_HTTP_TEMPLATE, DOMAIN=domain) + _fastapi_nextjs_server(
        domain=domain,
        cert=f'/etc/letsencrypt/live/{domain}/fullchain.pem',
        key=f'/etc/letsencrypt/live/{domain}/privkey.pem',
        resolver='127.0.0.11',
    )


def render_dev_nginx_conf(target_file: str):
    print_status(f"Generating dev nginx config to {target_file}")
    config = get_deploy_config()
    extra_domains_str = ' '.join(project_env.extra_domains) if len(project_env.extra_domains) > 0 else ''

    if config.is_fastapi and config.fastapi.frontend:
        conf_str = f"""
{custom_format(NGINX_BASE_REDIRECT_FASTAPI_TEMPLATE, PROJECT_DOMAIN=project_env.project_domain, EXTRA_DOMAINS=extra_domains_str)}
{HTTP_UPGRADE_TEMPLATE}
{_fastapi_nextjs_dev_server(project_env.project_domain, project_env.project_name)}
"""
    elif config.is_fastapi:
        conf_str = f"""
{custom_format(NGINX_BASE_REDIRECT_FASTAPI_TEMPLATE, PROJECT_DOMAIN=project_env.project_domain, EXTRA_DOMAINS=extra_domains_str)}
{HTTP_UPGRADE_TEMPLATE}
{custom_format(FASTAPI_DEV_APP_TEMPLATE, PROJECT_DOMAIN=project_env.project_domain, PROJECT_NAME=project_env.project_name)}
"""
    else:
        conf_str = f"""
{custom_format(NGINX_BASE_REDIRECT_TEMPLATE, PROJECT_DOMAIN=project_env.project_domain, EXTRA_DOMAINS=extra_domains_str)}
{HTTP_UPGRADE_TEMPLATE}
{custom_format(NGINX_DEV_MEDIA_TEMPLATE, PROJECT_NAME=project_env.project_name, PROJECT_DOMAIN=project_env.project_domain)}
{custom_format(NGINX_DEV_APP_TEMPLATE, PROJECT_DOMAIN=project_env.project_domain, PROJECT_NAME=project_env.project_name)}
"""

    with open(target_file, 'w') as f:
        f.write(conf_str)


def render_extra_dev_domain_nginx_conf(target_file: str, domain: str):
    print_status(f"Generating extra domain {domain} nginx config to {target_file}")
    config = get_deploy_config()
    if config.is_fastapi and config.fastapi.frontend:
        conf_str = _fastapi_nextjs_dev_server(domain, f'{project_env.project_name}_{domain}')
    else:
        template = NGINX_EXTRA_DEV_DOMAINS_FASTAPI_TEMPLATE if config.is_fastapi else NGINX_EXTRA_DEV_DOMAINS_TEMPLATE
        conf_str = custom_format(template, DOMAIN=domain, PROJECT_NAME=project_env.project_name)

    with open(target_file, 'w') as f:
        f.write(conf_str)


def render_centrifugo_dev_nginx_conf(target_file: str):
    print_status(f"Generating dev centrifugo nginx config to {target_file}")
    conf_str = custom_format(
        CENTRIFUGO_DEV_TEMPLATE,
        PROJECT_NAME=project_env.project_name,
        PROJECT_DOMAIN=project_env.project_domain,
    )

    with open(target_file, 'w') as f:
        f.write(conf_str)


def render_app_prod_nginx_conf(target_file: str):
    print_status(f"Generating prod nginx config to {target_file}")
    config = get_deploy_config()
    if config.is_fastapi and config.fastapi.frontend:
        conf_str = _with_upgrade_map(_fastapi_nextjs_prod_server(project_env.project_domain))
    elif config.is_fastapi:
        conf_str = _with_upgrade_map(custom_format(
            FASTAPI_PROD_APP_TEMPLATE,
            PROJECT_NAME=project_env.project_name,
            PROJECT_DOMAIN=project_env.project_domain,
        ))
    else:
        conf_str = custom_format(
            APP_PROD_TEMPLATE,
            PROJECT_NAME=project_env.project_name,
            PROJECT_DOMAIN=project_env.project_domain,
        )

    with open(target_file, 'w') as f:
        f.write(conf_str)


def render_centrifugo_prod_nginx_conf(target_file: str):
    print_status(f"Generating prod centrifugo nginx config to {target_file}")
    conf_str = custom_format(
        CENTRIFUGO_PROD_TEMPLATE,
        PROJECT_NAME=project_env.project_name,
        PROJECT_DOMAIN=project_env.project_domain,
    )

    with open(target_file, 'w') as f:
        f.write(conf_str)


def render_extra_domain_prod_nginx_conf(target_file: str, domain: str):
    print_status(f"Generating extra domain {domain} prod nginx config to {target_file}")
    config = get_deploy_config()
    if config.is_fastapi and config.fastapi.frontend:
        conf_str = _with_upgrade_map(_fastapi_nextjs_prod_server(domain))
    elif config.is_fastapi:
        conf_str = _with_upgrade_map(
            custom_format(EXTRA_DOMAIN_PROD_FASTAPI_TEMPLATE, DOMAIN=domain, PROJECT_NAME=project_env.project_name)
        )
    else:
        conf_str = custom_format(EXTRA_DOMAIN_PROD_TEMPLATE, DOMAIN=domain, PROJECT_NAME=project_env.project_name)

    with open(target_file, 'w') as f:
        f.write(conf_str)
