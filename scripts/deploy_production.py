import os
import shlex
import tempfile

from scripts.shell_commands import get_init_swarm_script
from scripts.commands import (
    copy_to_remote, get_image_hash, update_swarm, check_prod_https, ensure_balancer,
    collect_static, upload_images, login_registry, build_images, deployed_proxy_image,
)
from scripts import proxy_version
from scripts.compose.services import PROD_PROXY_SITES_DIR, prod_proxy_image
from scripts.helpers import run_command, run_remote_commands
from scripts.printing import print_status
from scripts.constants import (
    project_env, get_docker_image_prefix, check_registry_config, COMPOSE_DIR, BASE_ENV_FILE, PROD_ENV_FILE,
)
from scripts.deploy_config import DeployConfig, get_deploy_config
from scripts.release import release_version, update_version
from scripts.docker_compose import render_production_compose_file
from scripts.proxy.configuration import config_file_name, prod_sites, render_sites


def update_prod_proxy():
    """Validate the project's proxy config together with the other projects' configs on the server
    (`proxy -check`), then move it into place; the proxy applies it without a restart."""
    print_status(f"Updating the proxy config on {project_env.project_domain}")
    # Checked by the proxy that will read it: a newer running balancer may know more fields.
    check_image = proxy_version.check_image(deployed_proxy_image(), prod_proxy_image())
    name = config_file_name()
    staged = f"{PROD_PROXY_SITES_DIR}/.{name}.new"
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, name)
        with open(local, 'w', encoding='utf-8') as f:
            f.write(render_sites(prod_sites()))
        run_remote_commands([f"mkdir -p {PROD_PROXY_SITES_DIR}"], check=True)
        copy_to_remote(local, staged)
    run_remote_commands([f"""
        set -e
        CHECK_DIR=$(mktemp -d)
        trap 'rm -rf "$CHECK_DIR"' EXIT
        cp {PROD_PROXY_SITES_DIR}/*.yaml "$CHECK_DIR"/ 2>/dev/null || true
        cp {staged} "$CHECK_DIR"/{name}
        docker run --rm -v "$CHECK_DIR":/config:ro {check_image} -check -config /config
        mv {staged} {PROD_PROXY_SITES_DIR}/{name}
    """], check=True)


def _run_in_prod_image(component: str, command: str) -> None:
    run_remote_commands([
        f"docker run --rm -i --env-file={project_env.prod_app_path}/env.base "
        f"--env-file={project_env.prod_app_path}/env {get_docker_image_prefix()}-{component} "
        f"{command}"
    ])


def run_backend_migrations(config: DeployConfig) -> None:
    """Migrations of every enabled backend, independently of each other."""
    if config.django.enabled:
        print_status("Update django image for migrations")
        run_remote_commands([f"docker pull {get_docker_image_prefix()}-django"])
        print_status("Perform django migrations")
        _run_in_prod_image("django", "python manage.py migrate")
    if config.fastapi.enabled:
        print_status("Update fastapi image")
        run_remote_commands([f"docker pull {get_docker_image_prefix()}-fastapi"])
        if config.fastapi.migrate_command:
            print_status("Perform fastapi migrations")
            _run_in_prod_image("fastapi", f"sh -c {shlex.quote(config.fastapi.migrate_command)}")


def deploy_production():
    check_registry_config()
    config = get_deploy_config()
    new_version = update_version()

    build_images()
    login_registry()
    upload_images()

    # Release version after build to avoid debug releases
    release_version(new_version)

    collect_static()
    images = {
        component: get_image_hash(f'{get_docker_image_prefix()}-{component}')
        for component in config.components
    }
    render_production_compose_file(
        django_image=images.get('django', ''),
        fastapi_image=images.get('fastapi', ''),
        nextjs_image=images.get('nextjs', ''),
    )
    print_status(f"Deploying to {project_env.project_domain}")
    remote_dirs = [f"mkdir -p {project_env.prod_app_path}"]
    if config.django.enabled:
        remote_dirs.append(f"mkdir -p {project_env.prod_app_path}/backend_data")
    run_remote_commands(remote_dirs)

    print_status(f"Copying compose files to {project_env.project_domain}")

    copy_to_remote(f'{COMPOSE_DIR}/prod.yml', f'{project_env.prod_app_path}/prod.yml')
    run_command(f"rm {COMPOSE_DIR}/prod.yml")

    print_status(f"Copying env files to {project_env.project_domain}")
    copy_to_remote(BASE_ENV_FILE, f"{project_env.prod_app_path}/env.base")
    copy_to_remote(PROD_ENV_FILE, f"{project_env.prod_app_path}/env")
    run_remote_commands([get_init_swarm_script()])
    ensure_balancer()

    run_backend_migrations(config)

    update_swarm(f'{project_env.prod_app_path}/prod.yml', project_env.project_name)

    update_prod_proxy()
    check_prod_https()
