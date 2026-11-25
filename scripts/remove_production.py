"""`dc removeprod`: remove the project's runtime from the production server.

Removed: the swarm stack (services and their containers), the proxy site file (the proxy stops
serving the domains without a restart), the compose and env files of the project directory and
the project images on the server.

Kept: the database and S3 (not on the server), `backend_data` of the project directory, the shared
balancer with its certificates, the images in the registry. `dc deployprod` brings the project back.
"""
import shlex
from typing import get_args

from scripts.cli import Component
from scripts.compose.services import PROD_PROXY_SITES_DIR
from scripts.constants import get_docker_image_prefix, project_env
from scripts.helpers import run_remote_commands
from scripts.printing import print_status, print_warning
from scripts.proxy.configuration import config_file_name

# How long to wait for the containers of the removed stack to stop
STACK_REMOVE_TIMEOUT = 120


def removal_script(
    project_name: str, app_path: str, image_prefix: str, sites_dir: str = PROD_PROXY_SITES_DIR,
) -> str:
    stack = shlex.quote(project_name)
    site_file = f'{sites_dir}/{config_file_name()}'
    staged_site_file = f'{sites_dir}/.{config_file_name()}.new'
    # Exact repositories: a prefix match would also catch another project named "<name>-...".
    repositories = ' '.join(f'{image_prefix}-{component}' for component in get_args(Component))
    app = shlex.quote(app_path)
    attempts = STACK_REMOVE_TIMEOUT // 2
    return f"""
set -e
echo "Removing the proxy sites"
rm -f {shlex.quote(site_file)} {shlex.quote(staged_site_file)}

if docker stack ls --format '{{{{.Name}}}}' | grep -qx {stack}; then
    echo "Removing the {project_name} stack"
    docker stack rm {stack}
fi
for i in $(seq {attempts}); do
    if [ -z "$(docker ps -aq --filter label=com.docker.stack.namespace={stack})" ]; then
        break
    fi
    sleep 2
done
if [ -n "$(docker ps -aq --filter label=com.docker.stack.namespace={stack})" ]; then
    echo "Containers of the {project_name} stack are still running after {STACK_REMOVE_TIMEOUT}s" >&2
    exit 1
fi

echo "Removing the compose and env files"
rm -f {app}/prod.yml {app}/env.base {app}/env
# Only when empty: backend_data stays
rmdir {app} 2>/dev/null || true

echo "Removing the project images"
IMAGES=$(docker images --no-trunc --format '{{{{.ID}}}} {{{{.Repository}}}}' \\
    | awk -v repos={shlex.quote(repositories)} 'BEGIN {{ split(repos, r, " "); for (i in r) keep[r[i]] = 1 }} $2 in keep {{ print $1 }}' \\
    | sort -u)
if [ -n "$IMAGES" ]; then
    docker rmi -f $IMAGES
fi
"""


def confirmed(args: list[str]) -> bool:
    if '--yes' in args:
        return True
    name = project_env.project_name
    print_warning(
        f'This removes {name} from {project_env.project_domain}: its stack, proxy sites, compose and env '
        f'files and images. The database, S3 and {project_env.prod_app_path}/backend_data are kept.'
    )
    try:
        answer = input(f'Type the project name ({name}) to continue: ')
    except EOFError:
        return False
    return answer.strip() == name


def remove_production(args: list[str]) -> None:
    unknown = [arg for arg in args if arg != '--yes']
    if unknown:
        raise SystemExit('usage: dc removeprod [--yes]')
    if not confirmed(args):
        raise SystemExit('Cancelled')
    print_status(f'Removing {project_env.project_name} from {project_env.project_domain}')
    run_remote_commands([
        removal_script(project_env.project_name, project_env.prod_app_path, get_docker_image_prefix()),
    ], check=True)
    print_status(f'{project_env.project_name} is removed; `dc deployprod` deploys it again')
