"""Development commands: docker compose for the project and the shared balancer."""
import os
import subprocess
from collections.abc import Sequence
from typing import IO

from scripts.compose.services import POSTGRES_IMAGE, backend_internal_url
from scripts.constants import project_env
from scripts.deploy_config import get_deploy_config
from scripts.dev_balancer import ensure_dev_balancer, generate_dev_certs, update_dev_nginx
from scripts.paths import BACKUP_DIR, BASE_ENV_FILE, DEV_ENV_FILE, GENERATED_COMPOSE_DIR
from scripts.printing import print_status

DEV_SUPERUSER_PASSWORD = 'devpass'


def run(args: Sequence[str], *, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    """Run a command; a non-zero exit stops `dc` with the same code."""
    subprocess.run(list(args), check=True, stdin=stdin, stdout=stdout)


def dev_compose(*args: str) -> list[str]:
    return [
        'docker', 'compose', '-f', os.path.join(GENERATED_COMPOSE_DIR, 'dev.yml'),
        '--env-file', BASE_ENV_FILE, '--env-file', DEV_ENV_FILE, *args,
    ]


def balancer_compose(*args: str) -> list[str]:
    return ['docker', 'compose', '-f', os.path.join(GENERATED_COMPOSE_DIR, 'dev_balancer.yml'), *args]


def compose(args: list[str]) -> None:
    """Anything that is not a `dc` command goes to docker compose with the dev compose file."""
    run(dev_compose(*args))


def up(_args: list[str]) -> None:
    # Certificates, /etc/hosts and the project's nginx config are created/updated only when
    # missing or outdated (sudo is asked only then); a changed config is validated first.
    restart_balancer = ensure_dev_balancer()
    run(balancer_compose('up', '-d'))
    if restart_balancer:
        run(balancer_compose('restart'))
    run(dev_compose('up', '-d'))
    # Recreated containers get new IPs, while nginx keeps resolved upstream addresses for
    # up to 30s (resolver valid=30s) and answers 502; a reload drops the cached addresses.
    run(balancer_compose('exec', 'nginx', 'nginx', '-s', 'reload'))


def rebuild(_args: list[str]) -> None:
    config = get_deploy_config()
    run(dev_compose('down'))
    run(dev_compose('build', *config.components))
    if config.nextjs.enabled:
        run(dev_compose('run', '--rm', '-i', 'nextjs', 'sh', '-c', 'rm -rf node_modules package-lock.json && npm install'))
    run(dev_compose('up', '-d'))


def migrate(args: list[str]) -> None:
    config = get_deploy_config()
    if config.django.enabled:
        run(dev_compose('run', '--rm', 'django', 'python', 'manage.py', 'migrate', *args))
    if config.fastapi.enabled and config.fastapi.migrate_command:
        run(dev_compose('run', '--rm', 'fastapi', 'sh', '-c', config.fastapi.migrate_command))


def test(_args: list[str]) -> None:
    config = get_deploy_config()
    for backend in config.backends:
        run(dev_compose('run', '--rm', backend, 'pytest'))


def shell(args: list[str]) -> None:
    if len(args) != 1:
        raise SystemExit('usage: dc sh <service>')
    run(dev_compose('exec', args[0], 'bash'))


def django(args: list[str]) -> None:
    run(dev_compose('run', '--rm', 'django', 'python', 'manage.py', *args))


def format_code(_args: list[str]) -> None:
    run(dev_compose('run', '--rm', 'django', 'sh', '-c', 'isort . && black .'))


def devsuperuser(args: list[str]) -> None:
    if len(args) != 1:
        raise SystemExit('usage: dc devsuperuser <username>')
    username = args[0]
    run(dev_compose(
        'run', '--rm', '-e', f'DJANGO_SUPERUSER_PASSWORD={DEV_SUPERUSER_PASSWORD}', 'django',
        'python', 'manage.py', 'createsuperuser', '--noinput',
        '--username', username, '--email', f'{username}@{username}.com',
    ))


def generatetypes(_args: list[str]) -> None:
    backend = get_deploy_config().nextjs.backend
    assert backend is not None
    url = f'{backend_internal_url(backend)}/api/openapi.json'
    run(dev_compose('exec', 'nextjs', 'npx', '-y', 'openapi-typescript', url, '--output', 'api/apiTypes.ts'))


def nginx(args: list[str]) -> None:
    run(balancer_compose(*args))


def updatenginx(_args: list[str]) -> None:
    update_dev_nginx()
    run(balancer_compose('restart'))


def gencerts(_args: list[str]) -> None:
    generate_dev_certs()
    run(balancer_compose('restart'))


def tmux(_args: list[str]) -> None:
    """tmux session with the logs of the balancer and of every component."""
    session = f'{project_env.project_name}dev'
    window = f'{session}:1'
    subprocess.run(['tmux', 'kill-session', '-t', session], stderr=subprocess.DEVNULL)
    run(['tmux', 'new-session', '-d', '-s', session])
    run(['tmux', 'new-window', '-t', window, '-n', 'Server'])
    run(['tmux', 'kill-window', '-t', f'{session}:0'])
    run(['tmux', 'send-keys', '-t', window, 'pc nginx logs nginx -f', 'C-m'])
    for component in get_deploy_config().components:
        run(['tmux', 'split-window', '-v', '-t', window])
        run(['tmux', 'send-keys', '-t', window, f'pc logs {component} -f', 'C-m'])
    run(['tmux', 'select-layout', '-t', window, 'tiled'])
    run(['tmux', 'attach', '-t', session])


def _database(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f'{key} is not set in the environment files')
    return value


def dumpdev(_args: list[str]) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f'{project_env.project_name}dev.sql')
    print_status(f'Dumping the dev database to {path}')
    with open(path, 'w', encoding='utf-8') as out:
        run(dev_compose('exec', '-T', 'postgres', 'pg_dump', f'--username={project_env.project_name}', project_env.project_name), stdout=out)


def restoredev(_args: list[str]) -> None:
    path = os.path.join(BACKUP_DIR, f'{project_env.project_name}dev.sql')
    print_status(f'Restoring the dev database from {path}')
    with open(path, encoding='utf-8') as dump:
        run(
            [
                'docker', 'run', '--rm', '-i', f'--network={project_env.project_name}',
                '-e', f'PGPASSWORD={_database("DATABASE_PASSWORD")}', POSTGRES_IMAGE,
                'psql', '-U', _database('DATABASE_USER'), '-h', _database('DATABASE_HOST'),
                '-p', _database('DATABASE_PORT'), _database('DATABASE_NAME'),
            ],
            stdin=dump,
        )


def updatesubmodules(_args: list[str]) -> None:
    run(['git', 'submodule', 'status'])
    run(['git', 'submodule', 'update', '--init', '--recursive', '--remote'])
