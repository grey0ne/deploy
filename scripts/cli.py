"""`dc` command line: one registry of commands, each with the environment it runs in.

A command declares:
- ``env``: which env files are loaded (``dev``: env.base + env.dev, ``prod``: env.base +
  env.prod, ``None``: none) — production commands never see dev settings and vice versa;
- ``compose``: whether it uses the dev compose files, which are then regenerated first (only
  dev commands do; deploying production does not touch them);
- ``requires``: a component that must be enabled in deploy.json.

Handlers are ``module:function`` strings imported after the env files are loaded:
``scripts.constants`` reads the environment on import.
"""
import importlib
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from scripts.env_files import EnvFileError, EnvKind, load_environment
from scripts.errors import EnvConfigError
from scripts.printing import print_error

Component = Literal['django', 'fastapi', 'nextjs']
Group = Literal['Development', 'Data', 'Production']


@dataclass(frozen=True, slots=True, kw_only=True)
class Command:
    names: tuple[str, ...]
    description: str
    handler: str
    group: Group
    env: EnvKind | None
    compose: bool = False
    requires: Component | None = None
    # Plain functions take no arguments; the other handlers get the rest of argv.
    takes_args: bool = True

    @property
    def name(self) -> str:
        return self.names[0]


def command(
    names: str,
    description: str,
    handler: str,
    *,
    group: Group = 'Development',
    env: EnvKind | None = 'dev',
    compose: bool = False,
    requires: Component | None = None,
    takes_args: bool = True,
) -> Command:
    return Command(
        names=tuple(names.split()), description=description, handler=handler, group=group, env=env,
        compose=compose, requires=requires, takes_args=takes_args,
    )


DEV = 'scripts.dev_commands'
PROD = 'scripts.prod_tools'
OPS = 'scripts.commands'

COMMANDS: tuple[Command, ...] = (
    # Development
    command('up', 'Start the dev balancer and the project (certificates, hosts, nginx config first)',
            f'{DEV}:up', compose=True),
    command('rebuild', 'Rebuild images; Next.js: delete package-lock.json and node_modules, npm install',
            f'{DEV}:rebuild', compose=True),
    command('migrate', 'Django migrate and/or fastapi.migrate_command', f'{DEV}:migrate', compose=True),
    command('test', 'pytest in each backend container', f'{DEV}:test', compose=True),
    command('generatetypes', 'OpenAPI types into nextjs_frontend/api/apiTypes.ts', f'{DEV}:generatetypes',
            compose=True, requires='nextjs'),
    command('sh', 'bash in a running container: dc sh <service>', f'{DEV}:shell', compose=True),
    command('tmux', 'tmux session with the logs of the balancer and every component', f'{DEV}:tmux'),
    command('nginx', 'docker compose for the shared balancer: dc nginx logs -f nginx', f'{DEV}:nginx', compose=True),
    command('updatenginx update_dev_nginx', 'Validate, write and reload the balancer config', f'{DEV}:updatenginx',
            compose=True),
    command('gencerts', 'Regenerate and trust all dev certificates (+ /etc/hosts), restart the balancer',
            f'{DEV}:gencerts', compose=True),
    command('django', 'manage.py in the Django container: dc django <args>', f'{DEV}:django', compose=True,
            requires='django'),
    command('format', 'isort + black in the Django container', f'{DEV}:format_code', compose=True,
            requires='django'),
    command('devsuperuser', 'Django superuser with password devpass: dc devsuperuser <name>',
            f'{DEV}:devsuperuser', compose=True, requires='django'),
    command('generate_compose', 'Generate the dev compose files from deploy.json',
            'scripts.compose.generate:generate_dev_compose_files', takes_args=False),
    command('updatesubmodules', 'git submodule update --init --recursive --remote', f'{DEV}:updatesubmodules',
            env=None),
    # Data
    command('dumpdev', 'Dev Postgres to backup/<project>dev.sql', f'{DEV}:dumpdev', group='Data', compose=True),
    command('restoredev', 'Dev Postgres from backup/<project>dev.sql', f'{DEV}:restoredev', group='Data'),
    command('dumpprod', 'Production Postgres to backup/<project>.sql', f'{PROD}:dumpprod', group='Data', env='prod'),
    command('restoreprod', 'Production Postgres from a dump: dc restoreprod <database> <file>',
            f'{PROD}:restoreprod', group='Data', env='prod'),
    command('s3backupdev', 'Dev S3 files to a local directory', f'{OPS}:create_s3_dev_backup', group='Data',
            takes_args=False),
    command('s3restoredev', 'Local S3 backup to the dev S3', f'{OPS}:restore_s3_dev_backup', group='Data',
            takes_args=False),
    command('s3backupprod', 'Production S3 files to a local directory', f'{OPS}:create_s3_prod_backup',
            group='Data', env='prod', takes_args=False),
    command('s3restoreprod', 'Local S3 backup to the production S3', f'{OPS}:restore_s3_prod_backup',
            group='Data', env='prod', takes_args=False),
    command('s3createdevbucket', 'Create the dev S3 bucket', f'{OPS}:create_s3_dev_bucket', group='Data',
            takes_args=False),
    # Production
    command('deployprod deploy', 'Build, push and deploy the production stack (certificates, migrations, nginx)',
            'scripts.deploy_production:deploy_production', group='Production', env='prod', takes_args=False),
    command('certprod issue_certs', 'Issue missing SSL certificates for the production domains',
            f'{OPS}:issue_prod_certs', group='Production', env='prod', takes_args=False),
    command('initprod prod_setup production_setup', 'Set up Docker, the balancer and certificates on the server',
            f'{OPS}:production_setup', group='Production', env='prod', takes_args=False),
    command('deploybalancer setup_balancer', 'Set up the production balancer', f'{OPS}:setup_balancer',
            group='Production', env='prod', takes_args=False),
    command('initinfra', 'Create the DigitalOcean droplet and database', 'scripts.do_init:init_do_infra',
            group='Production', env='prod', takes_args=False),
    command('djangoprod', 'manage.py in the production Django image: dc djangoprod <args>', f'{PROD}:djangoprod',
            group='Production', env='prod', requires='django'),
    command('fastapiprod', 'A command in the production FastAPI image: dc fastapiprod <command>',
            f'{PROD}:fastapiprod', group='Production', env='prod', requires='fastapi'),
    command('logsprod', 'Logs of a production service: dc logsprod <service>', f'{PROD}:logsprod',
            group='Production', env='prod'),
)

# Anything that is not a command goes to docker compose with the dev compose file.
COMPOSE_PASSTHROUGH = command('compose', 'docker compose with the dev compose file', f'{DEV}:compose', compose=True)


def index_commands(commands: tuple[Command, ...]) -> dict[str, Command]:
    index: dict[str, Command] = {}
    for item in commands:
        for name in item.names:
            if name in index:
                raise ValueError(f'Duplicate command name: {name}')
            index[name] = item
    return index


COMMANDS_BY_NAME = index_commands(COMMANDS)


def print_help() -> None:
    print('usage: dc <command> [args...]   (anything else goes to docker compose with the dev compose file)')
    for group in ('Development', 'Data', 'Production'):
        print(f'\n{group}:')
        for item in COMMANDS:
            if item.group == group:
                print(f'  {item.name:<18} {item.description}')


def _resolve(handler: str) -> Callable[..., None]:
    module_name, _, function = handler.partition(':')
    return getattr(importlib.import_module(module_name), function)


def execute(item: Command, args: list[str]) -> None:
    if item.env is not None:
        load_environment(item.env)
        from scripts.deploy_config import get_deploy_config

        config = get_deploy_config()  # validates deploy.json for every project command
        if item.requires is not None and not getattr(config, item.requires).enabled:
            raise SystemExit(f"'{item.requires}' is not enabled in deploy.json")
    if item.compose:
        from scripts.compose.generate import generate_dev_compose_files

        generate_dev_compose_files()
    handler = _resolve(item.handler)
    if item.takes_args:
        handler(args)
    elif args:
        raise SystemExit(f'dc {item.name} takes no arguments')
    else:
        handler()


def _report(exc: Exception) -> int | None:
    """Exit code for errors shown as a message; ``None`` for bugs (traceback)."""
    if isinstance(exc, subprocess.CalledProcessError):
        # The failed command has printed its own error.
        return exc.returncode or 1
    if isinstance(exc, (EnvFileError, EnvConfigError)):
        print_error(str(exc))
        return 1
    # Imported here: these modules need the environment.
    from scripts.deploy_config import DeployConfigError
    from scripts.dev_balancer import NginxConfigError

    if isinstance(exc, DeployConfigError):
        print_error(str(exc))
        return 1
    if isinstance(exc, NginxConfigError):
        print_error(f'New nginx config is invalid, the balancer was not touched: {exc}')
        return 1
    return None


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ('help', '-h', '--help'):
        print_help()
        return 0
    item = COMMANDS_BY_NAME.get(argv[0])
    args = argv[1:]
    if item is None:
        item, args = COMPOSE_PASSTHROUGH, argv
    try:
        execute(item, args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        code = _report(exc)
        if code is None:
            raise
        return code
    return 0
