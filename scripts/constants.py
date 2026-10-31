import os
from dataclasses import dataclass, field, fields

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEPLOY_DIR = os.path.join(PROJECT_DIR, 'deploy')
ENV_DIR = os.path.join(PROJECT_DIR, 'environment')
BACKEND_DIR = os.path.join(PROJECT_DIR, 'backend')
FASTAPI_DIR = PROJECT_DIR
COMPOSE_DIR = os.path.join(DEPLOY_DIR, 'compose')
SPA_DIR = os.path.join(PROJECT_DIR, 'spa')
VERSION_FILE = os.path.join(ENV_DIR, 'version')
BASE_ENV_FILE = os.path.join(ENV_DIR, 'env.base')
PROD_ENV_FILE = os.path.join(ENV_DIR, 'env.prod')
DEV_ENV_FILE = os.path.join(ENV_DIR, 'env.dev')

@dataclass(kw_only=True)
class Environment:
    verbose: bool = False
    project_domain: str
    project_name: str
    extra_domains: list[str] = field(default_factory=list)
    registry_username: str | None = None
    registry_password: str | None = None
    registry_hostname: str | None = None
    registry_namespace: str | None = None
    nginx_config_dir: str | None = None
    prod_app_path: str
    project_description: str
    compose_profiles: list[str] = field(default_factory=list)
    ssl_certs_dir: str | None = None
    pg_cluster_name: str
    pg_db_name: str
    pg_username: str
    do_token: str | None = None
    do_region: str | None = None
    do_droplet_os_image: str | None = None
    do_droplet_size: str | None = None
    do_droplet_name: str
    do_ssh_fingerprint: str | None = None
    sentry_org: str
    sentry_url: str
    sentry_release_token: str | None = None
    sentry_projects: str
    s3_endpoint_url: str
    s3_access_key_id: str | None = None
    s3_secret_key: str | None = None
    s3_media_bucket: str
    s3_acl: str
    s3_static_bucket: str
    collected_static_dir: str | None = None

def get_config_value(key: str, default: str | None = None) -> str:
    result = os.getenv(key)
    if result is None:
        if default is not None:
            return default
        raise ValueError(f'Variable {key} is required')
    return result


def read_env() -> Environment:
    project_name = get_config_value("PROJECT_NAME")
    sentry_org = get_config_value("SENTRY_ORG", 'grey')
    sentry_url = f'https://sentry.io/api/0/organizations/{sentry_org}/releases/'
    sentry_release_token = get_config_value("SENTRY_RELEASE_TOKEN", '')
    extra_domains_str = get_config_value("EXTRA_DOMAINS", default='')
    return Environment(
        verbose=os.getenv("VERBOSE", "false").lower() == "true",
        project_domain=get_config_value("PROJECT_DOMAIN"),
        project_name=project_name,
        extra_domains=extra_domains_str.split(',') if len(extra_domains_str) > 0 else [],
        registry_username=os.getenv("REGISTRY_USERNAME"),
        registry_password=os.getenv("REGISTRY_PASSWORD"),
        registry_hostname=os.getenv("REGISTRY_HOSTNAME"),
        registry_namespace=os.getenv("REGISTRY_NAMESPACE"),
        nginx_config_dir=get_config_value("NGINX_CONFIG_DIR", default=f'~/nginx_configs'),
        prod_app_path=f'/app/{project_name}',
        project_description=f"{project_name} project",
        compose_profiles=os.getenv("COMPOSE_PROFILES", "").split(',') if os.getenv("COMPOSE_PROFILES", "") else [],
        ssl_certs_dir=get_config_value("SSL_CERTS_DIR", default=f'~/ssl_certs'),
        pg_cluster_name=get_config_value("PG_CLUSTER_NAME", "communal-db"),
        pg_db_name=get_config_value("PG_DB_NAME", project_name),
        pg_username=get_config_value("PG_USERNAME", ""),
        do_token=get_config_value("DO_TOKEN", ''),
        do_region=get_config_value("DO_REGION", 'ams3'),
        do_droplet_os_image=get_config_value("DO_DROPLET_OS_IMAGE", default="debian-12-x64"),
        do_droplet_size=get_config_value("DO_SIZE", default="s-1vcpu-1gb"),
        do_droplet_name=get_config_value("DO_DROPLET_NAME", default=f"{project_name}-app"),
        do_ssh_fingerprint=get_config_value("DO_KEY_FINGERPRINT", ''),
        sentry_org=sentry_org,
        sentry_url=sentry_url,
        sentry_release_token=sentry_release_token,
        sentry_projects=str(os.getenv('SENTRY_PROJECTS', f'{project_name}-frontend,{project_name}-backend')),

        s3_endpoint_url = os.getenv('S3_ENDPOINT_URL', f'http://{project_name}-minio:9000'),
        s3_access_key_id = os.getenv('S3_ACCESS_KEY_ID', f'{project_name}_minio'),
        s3_secret_key = os.getenv('S3_SECRET_KEY', f'{project_name}_minio'),
        s3_media_bucket = os.getenv('S3_MEDIA_BUCKET', f'{project_name}-media'),
        s3_acl = os.getenv('S3_ACL', 'private'),
        s3_static_bucket = os.getenv('S3_STATIC_BUCKET', f'{project_name}-static'),
        collected_static_dir=os.path.join(BACKEND_DIR, 'collected_static')
    )

project_env = read_env()

def update_environment():
    global project_env
    from scripts.deploy_config import reset_deploy_config
    new_env = read_env()
    for field in fields(Environment):
        setattr(project_env, field.name, getattr(new_env, field.name))
    reset_deploy_config()

def get_env() -> Environment:
    return project_env

def get_docker_image_prefix():
    return f'{project_env.registry_hostname}/{project_env.registry_namespace}/{project_env.project_name}'

# Constants for DigitalOcean

DROPLET_TAGS = ["auto-created"]
PG_NODES_NUM = 1
PG_VERSION = "16"
PG_SIZE = "db-s-1vcpu-1gb"
PG_STORAGE_SIZE = 15000 # 15GB
STATUS_CHECK_INTERVAL = 1 # in seconds

DO_API_DOMAIN = "https://api.digitalocean.com"
DROPLETS_URL = f"{DO_API_DOMAIN}/v2/droplets"

def get_do_headers():
    return {
        "Authorization": f"Bearer {project_env.do_token}",
        "Content-Type": "application/json"
    }

