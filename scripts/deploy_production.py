from scripts.shell_commands import (
    get_init_swarm_script, RELOAD_NGINX
)
from scripts.commands import (
    copy_to_remote, get_image_hash, update_swarm,
    collect_static, upload_images, login_registry, build_images
)
from scripts.helpers import run_command, run_remote_commands
from scripts.printing import print_status
from scripts.constants import (
    project_env, get_docker_image_prefix, DEPLOY_DIR, COMPOSE_DIR, BASE_ENV_FILE, PROD_ENV_FILE,
)
from scripts.deploy_config import get_deploy_config
from scripts.release import release_version, update_version
from scripts.docker_compose import render_production_compose_file
from scripts.nginx.configuration import (
    render_app_prod_nginx_conf, render_centrifugo_prod_nginx_conf, render_extra_domain_prod_nginx_conf
)


def app_prod_nginx_conf():
    render_app_prod_nginx_conf(f'{DEPLOY_DIR}/nginx/conf/{project_env.project_name}.conf')
    copy_to_remote(f'{DEPLOY_DIR}/nginx/conf/{project_env.project_name}.conf', f'/app/balancer/conf/{project_env.project_name}.conf')
    run_command(f"rm {DEPLOY_DIR}/nginx/conf/{project_env.project_name}.conf")

def centrifugo_prod_nginx_conf():
    render_centrifugo_prod_nginx_conf(f'{DEPLOY_DIR}/nginx/conf/{project_env.project_name}_centrifugo.conf')
    copy_to_remote(f'{DEPLOY_DIR}/nginx/conf/{project_env.project_name}_centrifugo.conf', f'/app/balancer/conf/{project_env.project_name}_centrifugo.conf')
    run_command(f"rm {DEPLOY_DIR}/nginx/conf/{project_env.project_name}_centrifugo.conf")

def extra_domain_prod_nginx_conf(domain: str):
    render_extra_domain_prod_nginx_conf(f'{DEPLOY_DIR}/nginx/conf/{project_env.project_name}_{domain}.conf', domain)
    copy_to_remote(f'{DEPLOY_DIR}/nginx/conf/{project_env.project_name}_{domain}.conf', f'/app/balancer/conf/{project_env.project_name}_{domain}.conf')
    run_command(f"rm {DEPLOY_DIR}/nginx/conf/{project_env.project_name}_{domain}.conf")


def update_prod_nginx():
    print_status(f"Copying nginx config to {project_env.project_domain}")
    app_prod_nginx_conf()
    if get_deploy_config().centrifugo_enabled:
        centrifugo_prod_nginx_conf()
    for domain in project_env.extra_domains:
        extra_domain_prod_nginx_conf(domain)

    print_status("Reloading nginx")
    run_remote_commands([RELOAD_NGINX, ])


def deploy_production():
    config = get_deploy_config()
    new_version = update_version()

    build_images()
    login_registry()
    upload_images()

    # Release version after build to avoid debug releases
    release_version(new_version)

    if config.is_fastapi:
        fastapi_image = get_image_hash(f'{get_docker_image_prefix()}-fastapi')
        render_production_compose_file(fastapi_image=fastapi_image)
        print_status(f"Deploying to {project_env.project_domain}")
        run_remote_commands([f"mkdir -p {project_env.prod_app_path}"])
    else:
        collect_static()
        django_image = get_image_hash(f'{get_docker_image_prefix()}-django')
        nextjs_image = get_image_hash(f'{get_docker_image_prefix()}-nextjs')
        render_production_compose_file(django_image=django_image, nextjs_image=nextjs_image)
        print_status(f"Deploying to {project_env.project_domain}")
        run_remote_commands([
            f"mkdir -p {project_env.prod_app_path}",
            f"mkdir -p {project_env.prod_app_path}/backend_data",
        ])

    print_status(f"Copying compose files to {project_env.project_domain}")

    copy_to_remote(f'{COMPOSE_DIR}/prod.yml', f'{project_env.prod_app_path}/prod.yml')
    run_command(f"rm {COMPOSE_DIR}/prod.yml")

    copy_to_remote(f"{DEPLOY_DIR}/prod-scripts/certbot_renew.sh", "/etc/cron.daily")

    print_status(f"Copying env files to {project_env.project_domain}")
    copy_to_remote(BASE_ENV_FILE, f"{project_env.prod_app_path}/env.base")
    copy_to_remote(PROD_ENV_FILE, f"{project_env.prod_app_path}/env")
    run_remote_commands([get_init_swarm_script()])

    if config.is_fastapi:
        print_status("Update fastapi image")
        run_remote_commands([f"docker pull {get_docker_image_prefix()}-fastapi"])
    else:
        print_status("Update django image for migrations")
        run_remote_commands([f"docker pull {get_docker_image_prefix()}-django"])
        print_status("Perform migrations")
        run_remote_commands([
            f"docker run --rm -i --env-file={project_env.prod_app_path}/env.base "
            f"--env-file={project_env.prod_app_path}/env {get_docker_image_prefix()}-django "
            f"python manage.py migrate"
        ])

    update_swarm(f'{project_env.prod_app_path}/prod.yml', project_env.project_name)

    update_prod_nginx()
