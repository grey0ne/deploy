from scripts.constants import (
    project_env, get_docker_image_prefix, EnvConfigError, DEPLOY_DIR, DJANGO_DIR, FASTAPI_BUILD_CONTEXT,
    BASE_ENV_FILE, DEV_ENV_FILE, COMPOSE_DIR, PROD_ENV_FILE, NEXTJS_DIR,
)
from scripts.compose.fastapi_image import PROD_DOCKERFILE as FASTAPI_PROD_DOCKERFILE, write_fastapi_dockerfiles
from scripts.compose.renderer import render_prod_balancer_compose
from scripts.compose.services import GENERATED_DIR
from scripts.deploy_config import get_deploy_config
from scripts.helpers import run_command, run_remote_commands, remote_command_succeeds
from scripts.printing import print_status
from scripts.shell_commands import (
    PRINT_COMMAND, RELOAD_NGINX, get_login_registry_script, get_gen_fake_certs_script, get_init_balancer_script, SETUP_DOCKER
)
import subprocess
from subprocess import PIPE
import os

def get_s3_backup_command() -> str:
    return f"docker run -it --rm -v {DJANGO_DIR}/:/app/src -v {DEPLOY_DIR}/deploy:/app/deploy -v {DEPLOY_DIR}/backup/s3_backup:/tmp/s3_backup --network {project_env.project_name} --env-file {BASE_ENV_FILE}"

def restore_s3_backup(env_file: str):
    command = f"{get_s3_backup_command()} --env-file {env_file} {project_env.project_name}-django python /app/deploy/scripts/s3_upload.py"
    run_command(command)

def create_s3_backup(env_file: str):
    command = f"{get_s3_backup_command()} --env-file {env_file} {project_env.project_name}-django python /app/deploy/scripts/s3_backup.py"
    run_command(command)

def create_s3_dev_backup():
    create_s3_backup(DEV_ENV_FILE)

def restore_s3_dev_backup():
    restore_s3_backup(DEV_ENV_FILE)

def create_s3_prod_backup():
    create_s3_backup(PROD_ENV_FILE)

def restore_s3_prod_backup():
    restore_s3_backup(PROD_ENV_FILE)

def create_s3_dev_bucket():
    command = f"{get_s3_backup_command()} --env-file {DEV_ENV_FILE} {project_env.project_name}-django python /app/deploy/scripts/s3_create_bucket.py"
    run_command(command)

def copy_to_remote(source: str, destination: str):
    run_command(f'scp {source} root@{project_env.project_domain}:{destination}')

def get_image_hash(image_name: str) -> str:
    command = ["docker", "inspect", "--format={{index .RepoDigests 0}}", image_name]
    p = subprocess.run(command, stdout=PIPE, encoding='ascii')
    if p.stdout:
        return p.stdout.strip()
    return ""


def reload_prod_nginx():
    print_status("Reloading nginx")
    run_remote_commands([RELOAD_NGINX, ])

STACK_DEPLOY_TIMEOUT = 300

def update_swarm(compose_file: str, stack_name: str):
    print_status(f"Updating {stack_name} swarm")
    STACK_COMMAND = f"""
    {PRINT_COMMAND}
    if ! timeout {STACK_DEPLOY_TIMEOUT} sh -c "docker stack config -c {compose_file} | docker stack deploy --with-registry-auth --detach=false -c - {stack_name}"; then
        print_error "Stack {stack_name} did not start within {STACK_DEPLOY_TIMEOUT}s"
        for SERVICE in $(docker stack services -q {stack_name}); do
            docker service ps --no-trunc --format '{{{{.Name}}}}: {{{{.CurrentState}}}} {{{{.Error}}}}' $SERVICE | head -n 5
            timeout 10 docker service logs --tail 20 $SERVICE
        done
    fi
    """
    run_remote_commands([STACK_COMMAND, ])
    print_status("Prune images")
    run_remote_commands(['docker image prune -f',])

def setup_balancer():
    render_prod_balancer_compose()
    print_status("Copying balancer files")
    run_remote_commands([
        f"mkdir -p /app/balancer",
        f"mkdir -p /app/balancer/conf",
    ])
    print_status("Preparing swarm, network and certificates for balancer")
    run_remote_commands([get_init_balancer_script()])
    copy_to_remote(f'{GENERATED_DIR}/prod_balancer.yml', '/app/balancer/compose.yml')
    copy_to_remote(f'{DEPLOY_DIR}/nginx/conf/balancer.conf', '/app/balancer/conf/default.conf')
    update_swarm('/app/balancer/compose.yml', 'balancer')
    reload_prod_nginx()


def collect_static():
    if not get_deploy_config().django.enabled:
        return
    print_status("Collecting static files for django")
    run_command(
        f"docker run --rm -i --env-file={BASE_ENV_FILE} --env-file={PROD_ENV_FILE} -e BUILD_STATIC=true -v {DJANGO_DIR}:/app/src {project_env.project_name}-django python manage.py collectstatic --noinput"
    )
    print_status("Uploading static files to S3")
    run_command(
        f"docker run --rm -i --env-file={BASE_ENV_FILE} --env-file={PROD_ENV_FILE} -v {DJANGO_DIR}:/app/src -v {DEPLOY_DIR}:/app/deploy {project_env.project_name}-django python /app/deploy/scripts/collect_static.py"
    )

def upload_images():
    print_status("Uploading images to registry")
    for component in get_deploy_config().components:
        run_command(f"docker push {get_docker_image_prefix()}-{component}")

def login_registry():
    run_command(get_login_registry_script())

def build_image(service: str, dockerfile: str, context: str):
    print_status(f"Building image for {service}")
    command = f"""
        export DOCKER_CLI_HINTS="false"
        docker build -t {get_docker_image_prefix()}-{service} -f {dockerfile} {context}  --platform linux/amd64
    """
    run_command(command)

def build_images():
    config = get_deploy_config()
    if config.django.enabled:
        build_image("django", f"{DEPLOY_DIR}/docker/Dockerfile.djangoprod", DJANGO_DIR)
    if config.fastapi.enabled:
        write_fastapi_dockerfiles(config)
        build_image("fastapi", FASTAPI_PROD_DOCKERFILE, FASTAPI_BUILD_CONTEXT)
    if config.nextjs.enabled:
        # Nextjs build requires env file to hardcode NEXT_PUBLIC_* variables
        create_next_public_env_file()
        build_image("nextjs", f"{DEPLOY_DIR}/docker/Dockerfile.nextjsprod", NEXTJS_DIR)
        run_command(f"rm {NEXTJS_DIR}/.env")


def setup_prod_domain_cert(domain: str):
    print_status(f"Setting up certbot for domain {domain}")
    SETUP_CERTBOT_COMMAND = f"""
    CERTS_VOLUME=/app/certbot/certificates
    CHALLENGE_VOLUME=/app/certbot/challenge
    docker run --rm --name temp_certbot -v $CERTS_VOLUME:/etc/letsencrypt -v $CHALLENGE_VOLUME:/tmp/letsencrypt certbot/certbot:v1.14.0 certonly --non-interactive --webroot --agree-tos --keep-until-expiring --text --email sergey.lihobabin@gmail.com -d {domain} -w /tmp/letsencrypt
    """
    run_remote_commands([ SETUP_CERTBOT_COMMAND, ])


def get_prod_cert_domains() -> list[str]:
    domains = [project_env.project_domain, *project_env.extra_domains]
    if get_deploy_config().centrifugo_enabled:
        domains.append(f"centrifugo.{project_env.project_domain}")
    return domains

def get_missing_prod_certs() -> list[str]:
    return [
        domain for domain in get_prod_cert_domains()
        if not remote_command_succeeds(f"test -f /app/certbot/certificates/live/{domain}/fullchain.pem")
    ]

def issue_prod_certs():
    missing = get_missing_prod_certs()
    if not missing:
        print_status("All certificates are present")
        return
    run_remote_commands([
        f"mkdir -p /app/certbot/certificates",
        f"mkdir -p /app/certbot/challenge",
    ])
    for domain in missing:
        setup_prod_domain_cert(domain)
    # The balancer is shared between projects: a config pointing to a missing certificate
    # stops nginx from starting and takes down every site on the server
    still_missing = get_missing_prod_certs()
    if still_missing:
        raise EnvConfigError(
            f'Failed to issue certificates for {", ".join(still_missing)}. '
            f'Check that DNS records point to the server and balancer nginx is running'
        )

def setup_prod_certs():
    issue_prod_certs()
    print_status(f"Copying fake certs to dummy folder")
    run_remote_commands([ get_gen_fake_certs_script(), ])
    reload_prod_nginx()

def production_setup():
    print_status("Setting up docker")
    run_remote_commands([ SETUP_DOCKER, ])
    setup_balancer()
    setup_prod_certs()

def create_next_public_env_file() -> None:
    """
    Gets all environment variables with prefix "NEXT_PUBLIC_" and writes them to "nextjs_frontend/.env"
    """
    print_status("Creating .env file with NEXT_PUBLIC_ environment variables")
    
    # Get all environment variables with NEXT_PUBLIC_ prefix
    next_public_vars: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("NEXT_PUBLIC_"):
            next_public_vars[key] = value
    
    # Write environment variables to nextjs_frontend/.env
    env_file_path = os.path.join(NEXTJS_DIR, ".env")
    with open(env_file_path, 'w') as f:
        for key, value in next_public_vars.items():
            f.write(f"{key}={value}\n")