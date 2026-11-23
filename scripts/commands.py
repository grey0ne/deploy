from scripts.constants import (
    project_env, get_docker_image_prefix, check_registry_config, EnvConfigError, DEPLOY_DIR, DJANGO_DIR, FASTAPI_BUILD_CONTEXT,
    BASE_ENV_FILE, DEV_ENV_FILE, COMPOSE_DIR, PROD_ENV_FILE, NEXTJS_DIR,
)
from scripts.paths import BACKUP_DIR
from scripts.compose.fastapi_image import PROD_DOCKERFILE as FASTAPI_PROD_DOCKERFILE, write_fastapi_dockerfiles
from scripts.compose.renderer import render_prod_balancer_compose
from scripts.compose.services import (
    GENERATED_DIR, PROD_BALANCER_DIR, PROD_PROXY_ACME_DIR, PROD_PROXY_SITES_DIR, PROXY_DIR, prod_proxy_image,
)
from scripts.deploy_config import get_deploy_config
from scripts import swarm_progress
from scripts.helpers import run_command, run_remote_commands, remote_command_output, remote_command_succeeds
from scripts.printing import print_error, print_status, print_warning
from scripts.proxy.configuration import prod_sites, site_domains
from scripts.shell_commands import get_login_registry_script, get_init_balancer_script, SETUP_DOCKER
import shlex
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from subprocess import PIPE
import os

# Scripts run inside the Django image import `scripts.*`, so the deploy dir must be importable.
CONTAINER_DEPLOY_ENV = "-e PYTHONPATH=/app/deploy"


def get_s3_backup_command() -> str:
    return (
        f"docker run -it --rm -v {DJANGO_DIR}/:/app/src -v {DEPLOY_DIR}:/app/deploy -v {BACKUP_DIR}/s3_backup:/tmp/s3_backup "
        f"{CONTAINER_DEPLOY_ENV} --network {project_env.project_name} --env-file {BASE_ENV_FILE}"
    )

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


STACK_DEPLOY_TIMEOUT = 300

def update_swarm(compose_file: str, stack_name: str, prune: bool = False):
    """prune: remove the services of the stack that are no longer in the compose file."""
    print_status(f"Updating {stack_name} swarm")
    snapshot = swarm_progress.snapshot_script(stack_name)
    before = swarm_progress.parse_snapshot(remote_command_output(snapshot))
    prune_flag = " --prune" if prune else ""
    run_remote_commands([
        f"docker stack config -c {compose_file} | docker stack deploy --with-registry-auth --detach=true{prune_flag} -c - {stack_name}"
    ])
    converged = swarm_progress.wait_for_stack(
        before, lambda: swarm_progress.parse_snapshot(remote_command_output(snapshot)), STACK_DEPLOY_TIMEOUT,
    )
    if not converged:
        print_error(f"Stack {stack_name} did not converge (failed or timed out after {STACK_DEPLOY_TIMEOUT}s)")
        run_remote_commands([f"""
        for SERVICE in $(docker stack services -q {stack_name}); do
            docker service ps --no-trunc --format '{{{{.Name}}}}: {{{{.CurrentState}}}} {{{{.Error}}}}' $SERVICE | head -n 5
            timeout 10 docker service logs --tail 20 $SERVICE
        done
        """])
    print_status("Prune images")
    run_remote_commands(['docker image prune -f',])

def build_proxy_image():
    build_image("proxy", f"{PROXY_DIR}/Dockerfile", PROXY_DIR, image=prod_proxy_image())


def deployed_proxy_image() -> str | None:
    """Image of the running proxy service without its digest; None when the server runs no proxy."""
    output = remote_command_output(
        "docker service inspect balancer_proxy --format '{{.Spec.TaskTemplate.ContainerSpec.Image}}' "
        "2>/dev/null || true"
    )
    return output.strip().split('@')[0] or None


def start_balancer():
    """Start the proxy of PROXY_VERSION from the registry; it replaces the nginx balancer (pruned)."""
    render_prod_balancer_compose()
    print_status("Preparing swarm, network and directories for the balancer")
    run_remote_commands([
        get_init_balancer_script(),
        # The server pulls the proxy image from the registry
        get_login_registry_script(),
        f"mkdir -p {PROD_PROXY_SITES_DIR} {PROD_PROXY_ACME_DIR}",
        "chmod 700 " + PROD_PROXY_ACME_DIR,
        # Renewals were done by certbot (and run-parts skips names with dots): the proxy renews now.
        "rm -f /etc/cron.daily/certbot_renew.sh",
    ], check=True)
    copy_to_remote(f'{GENERATED_DIR}/prod_balancer.yml', f'{PROD_BALANCER_DIR}/compose.yml')
    update_swarm(f'{PROD_BALANCER_DIR}/compose.yml', 'balancer', prune=True)


def setup_balancer():
    """Build and push the proxy image like the project images, then (re)start the balancer."""
    check_registry_config()
    build_proxy_image()
    login_registry()
    run_command(f"docker push {prod_proxy_image()}")
    start_balancer()


def ensure_balancer():
    """Rolls out a new PROXY_VERSION (its image is built and pushed with the project images).

    A server still running the nginx balancer is not switched: it serves the sites of every
    project, which keep working only once each of them has uploaded its proxy config. The switch
    is `dc deploybalancer`.
    """
    deployed = deployed_proxy_image()
    if deployed == prod_proxy_image():
        return
    if deployed:
        print_status(f"Updating the balancer: {deployed} -> {prod_proxy_image()}")
        start_balancer()
        return
    print_warning(
        "The server runs the nginx balancer: it keeps serving the sites. After every project on the "
        "server has deployed with this deploy version (each uploads its proxy config), switch to the "
        "proxy with `dc deploybalancer`."
    )


def collect_static():
    if not get_deploy_config().django.enabled:
        return
    print_status("Collecting static files for django")
    run_command(
        f"docker run --rm -i --env-file={BASE_ENV_FILE} --env-file={PROD_ENV_FILE} -e BUILD_STATIC=true -v {DJANGO_DIR}:/app/src {project_env.project_name}-django python manage.py collectstatic --noinput"
    )
    print_status("Uploading static files to S3")
    run_command(
        f"docker run --rm -i --env-file={BASE_ENV_FILE} --env-file={PROD_ENV_FILE} -v {DJANGO_DIR}:/app/src -v {DEPLOY_DIR}:/app/deploy {CONTAINER_DEPLOY_ENV} {project_env.project_name}-django python /app/deploy/scripts/collect_static.py"
    )

def upload_images():
    print_status("Uploading images to registry")
    for component in get_deploy_config().components:
        run_command(f"docker push {get_docker_image_prefix()}-{component}")
    run_command(f"docker push {prod_proxy_image()}")

def login_registry():
    run_command(get_login_registry_script())

def build_image(
    service: str, dockerfile: str, context: str, extra_args: list[str] | None = None, image: str | None = None,
):
    print_status(f"Building image for {service}")
    args = ' '.join(shlex.quote(arg) for arg in extra_args or [])
    image = image or f"{get_docker_image_prefix()}-{service}"
    command = f"""
        export DOCKER_CLI_HINTS="false"
        docker build -t {image} -f {dockerfile} {args} {context}  --platform linux/amd64
    """
    run_command(command)


def nextjs_sentry_build_args() -> list[str]:
    """Build args and secret for the Sentry source maps upload in the Next.js build (next.config.ts)."""
    args = [
        '--build-arg', f'PROJECT_NAME={project_env.project_name}',
        '--build-arg', f'SENTRY_ORG={project_env.sentry_org}',
        '--build-arg', f'SENTRY_RELEASE={os.environ.get("NEXT_PUBLIC_VERSION", "")}',
    ]
    if project_env.sentry_release_token:
        # Read by docker from the environment of this process: the token never appears on the command line
        args += ['--secret', 'id=sentry_release_token,env=SENTRY_RELEASE_TOKEN']
    else:
        print_warning('SENTRY_RELEASE_TOKEN is not set. Frontend source maps will not be uploaded to Sentry.')
    return args

def build_images():
    config = get_deploy_config()
    if config.django.enabled:
        build_image(
            "django", f"{DEPLOY_DIR}/docker/Dockerfile.djangoprod", DJANGO_DIR,
            ["--build-arg", f"PYTHON_IMAGE={config.django.python_image}"],
        )
    if config.fastapi.enabled:
        write_fastapi_dockerfiles(config)
        build_image("fastapi", FASTAPI_PROD_DOCKERFILE, FASTAPI_BUILD_CONTEXT)
    if config.nextjs.enabled:
        # Nextjs build requires env file to hardcode NEXT_PUBLIC_* variables
        create_next_public_env_file()
        build_image(
            "nextjs", f"{DEPLOY_DIR}/docker/Dockerfile.nextjsprod", NEXTJS_DIR, nextjs_sentry_build_args(),
        )
        run_command(f"rm {NEXTJS_DIR}/.env")
    build_proxy_image()


HTTPS_CHECK_TIMEOUT = 180


def _https_error(domain: str) -> str | None:
    """None when the domain answers over https with a valid certificate (any HTTP status)."""
    try:
        urllib.request.urlopen(f"https://{domain}/", timeout=10)
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, ssl.SSLError, OSError) as exc:
        return str(getattr(exc, 'reason', exc))
    return None


def check_prod_https() -> bool:
    """Wait until every production domain serves a valid certificate. The proxy requests a
    certificate as soon as a domain appears in its config; issuance needs DNS pointing to the
    server. Returns False (with a warning) when some domain is still failing."""
    pending = site_domains(prod_sites())
    deadline = time.monotonic() + HTTPS_CHECK_TIMEOUT
    errors: dict[str, str] = {}
    while pending:
        errors = {domain: error for domain in pending if (error := _https_error(domain)) is not None}
        for domain in pending:
            if domain not in errors:
                print_status(f"https://{domain}/ serves a valid certificate")
        pending = list(errors)
        if not pending or time.monotonic() > deadline:
            break
        time.sleep(5)
    for domain, error in errors.items():
        print_warning(
            f"https://{domain}/ is not available: {error}. Check that DNS points to the server and "
            f"the proxy logs: ssh root@{project_env.project_domain} docker service logs balancer_proxy"
        )
    return not errors


def production_setup():
    print_status("Setting up docker")
    run_remote_commands([ SETUP_DOCKER, ])
    setup_balancer()

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