from scripts.constants import (
    project_env, get_docker_image_prefix, DEPLOY_DIR, BACKEND_DIR, FASTAPI_DIR,
    BASE_ENV_FILE, DEV_ENV_FILE, COMPOSE_DIR, PROD_ENV_FILE, SPA_DIR,
)
from scripts.compose.renderer import render_prod_balancer_compose
from scripts.compose.services import GENERATED_DIR
from scripts.deploy_config import get_deploy_config
from scripts.helpers import run_command, run_remote_commands
from scripts.printing import print_status
from scripts.shell_commands import RELOAD_NGINX, get_login_registry_script, get_gen_fake_certs_script, SETUP_DOCKER
from scripts.nginx.configuration import (
    render_dev_nginx_conf, render_centrifugo_dev_nginx_conf, render_extra_dev_domain_nginx_conf
)
import subprocess
from subprocess import PIPE
import os

def get_s3_backup_command() -> str:
    return f"docker run -it --rm -v {BACKEND_DIR}/:/app/src -v {DEPLOY_DIR}/deploy:/app/deploy -v {DEPLOY_DIR}/backup/s3_backup:/tmp/s3_backup --network {project_env.project_name} --env-file {BASE_ENV_FILE}"

def restore_s3_backup(env_file: str):
    command = f"{get_s3_backup_command()} --env-file {env_file} {project_env.project_name}-django python /app/deploy/scripts/s3_upload.py"
    run_command(command)

def create_s3_backup(env_file: str):
    command = f"{get_s3_backup_command()} --env-file {env_file} {project_env.project_name}-django python /app/deploy/scripts/s3_backup.py"
    run_command(command)

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

def update_swarm(compose_file: str, stack_name: str):
    print_status(f"Updating {stack_name} swarm")
    STACK_COMMAND = f"docker stack config -c {compose_file} | docker stack deploy --with-registry-auth --detach=false -c - {stack_name}"
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
    copy_to_remote(f'{GENERATED_DIR}/prod_balancer.yml', '/app/balancer/compose.yml')
    copy_to_remote(f'{DEPLOY_DIR}/nginx/conf/balancer.conf', '/app/balancer/conf/default.conf')
    update_swarm('/app/balancer/compose.yml', 'balancer')
    reload_prod_nginx()


def update_dev_nginx():
    run_command(
        f"mkdir -p {project_env.nginx_config_dir}"
    )
    render_dev_nginx_conf(f"{project_env.nginx_config_dir}/{project_env.project_name}.conf.template")
    if get_deploy_config().centrifugo_enabled:
        render_centrifugo_dev_nginx_conf(f"{project_env.nginx_config_dir}/{project_env.project_name}_centrifugo.conf.template")
    for domain in project_env.extra_domains:
        render_extra_dev_domain_nginx_conf(f"{project_env.nginx_config_dir}/{project_env.project_name}_{domain}.conf.template", domain)

def collect_static():
    if get_deploy_config().is_fastapi:
        return
    print_status("Collecting static files for django")
    run_command(
        f"docker run --rm -i --env-file={BASE_ENV_FILE} --env-file={PROD_ENV_FILE} -e BUILD_STATIC=true -v ./backend:/app/src {project_env.project_name}-django python manage.py collectstatic --noinput"
    )
    print_status("Uploading static files to S3")
    run_command(
        f"docker run --rm -i --env-file={BASE_ENV_FILE} --env-file={PROD_ENV_FILE} -v ./backend:/app/src -v {DEPLOY_DIR}:/app/deploy {project_env.project_name}-django python /app/deploy/scripts/collect_static.py"
    )

def upload_images():
    print_status("Uploading images to registry")
    config = get_deploy_config()
    if config.is_fastapi:
        run_command(f"docker push {get_docker_image_prefix()}-fastapi")
        return
    run_command(f"docker push {get_docker_image_prefix()}-django")
    run_command(f"docker push {get_docker_image_prefix()}-nextjs")

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
    if config.is_fastapi:
        build_image(
            "fastapi",
            f"{DEPLOY_DIR}/docker/Dockerfile.fastapiprod",
            FASTAPI_DIR,
        )
        return
    build_image("django", f"{DEPLOY_DIR}/docker/Dockerfile.djangoprod", BACKEND_DIR)

    # Nextjs build requires env file to hardcode NEXT_PUBLIC_* variables
    create_next_public_env_file()
    build_image("nextjs", f"{DEPLOY_DIR}/docker/Dockerfile.nextjsprod", SPA_DIR)
    run_command(f"rm {SPA_DIR}/.env")


def gen_cert(name:str, domain:str):
    print_status(f"Generating cert {name} for {domain}")
    run_command(
        f"{DEPLOY_DIR}/generate_certs.sh {name} {domain}"
    )

def add_cert_to_trusted(cert_path: str):
    print_status(f"Adding cert to trusted {cert_path}")
    run_command(
        f"sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain {cert_path}"
    )

def update_hosts(domain: str):
    print_status(f"Updating hosts file with {domain}")
    run_command(
        f"{DEPLOY_DIR}/update_hosts.sh addhost {domain}"
    )

def generate_dev_certs():
    config = get_deploy_config()
    gen_cert(project_env.project_name, project_env.project_domain)
    add_cert_to_trusted(f"{project_env.ssl_certs_dir}/{project_env.project_name}.crt")
    update_hosts(project_env.project_domain)
    if not config.is_fastapi:
        gen_cert(f"media_{project_env.project_name}", f"media.{project_env.project_domain}")
        add_cert_to_trusted(f"{project_env.ssl_certs_dir}/media_{project_env.project_name}.crt")
        update_hosts(f"media.{project_env.project_domain}")
    for domain in project_env.extra_domains:
        cert_name = f"{project_env.project_name}_{domain}"
        gen_cert(cert_name, domain)
        add_cert_to_trusted(f"{project_env.ssl_certs_dir}/{cert_name}.crt")
        update_hosts(domain)
    if get_deploy_config().centrifugo_enabled:
        gen_cert(f"centrifugo_{project_env.project_name}", f"centrifugo.{project_env.project_domain}")
        add_cert_to_trusted(f"{project_env.ssl_certs_dir}/centrifugo_{project_env.project_name}.crt")
        update_hosts(f"centrifugo.{project_env.project_domain}")


def setup_prod_domain_cert(domain: str):
    print_status(f"Setting up certbot for domain {domain}")
    SETUP_CERTBOT_COMMAND = f"""
    CERTS_VOLUME=/app/certbot/certificates
    CHALLENGE_VOLUME=/app/certbot/challenge
    docker run --rm --name temp_certbot -v $CERTS_VOLUME:/etc/letsencrypt -v $CHALLENGE_VOLUME:/tmp/letsencrypt certbot/certbot:v1.14.0 certonly --non-interactive --webroot --agree-tos --keep-until-expiring --text --email sergey.lihobabin@gmail.com -d {domain} -w /tmp/letsencrypt
    """
    run_remote_commands([ SETUP_CERTBOT_COMMAND, ])


def setup_prod_certs():
    run_remote_commands([
        f"mkdir -p /app/certbot/certificates",
        f"mkdir -p /app/certbot/challenge",
    ])
    setup_prod_domain_cert(project_env.project_domain)
    for domain in project_env.extra_domains:
        setup_prod_domain_cert(domain)
    if get_deploy_config().centrifugo_enabled:
        setup_prod_domain_cert(f"centrifugo.{project_env.project_domain}")

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
    Gets all environment variables with prefix "NEXT_PUBLIC_" and writes them to "/spa/.env"
    """
    print_status("Creating .env file with NEXT_PUBLIC_ environment variables")
    
    # Get all environment variables with NEXT_PUBLIC_ prefix
    next_public_vars: dict[str, str] = {}
    for key, value in os.environ.items():
        if key.startswith("NEXT_PUBLIC_"):
            next_public_vars[key] = value
    
    # Write environment variables to /spa/.env
    env_file_path = os.path.join(SPA_DIR, ".env")
    with open(env_file_path, 'w') as f:
        for key, value in next_public_vars.items():
            f.write(f"{key}={value}\n")