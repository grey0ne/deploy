"""One-off production commands over ssh: database dumps, commands in the app images, logs."""
import os
import shlex
import subprocess
from collections.abc import Sequence
from typing import IO

from scripts.compose.services import POSTGRES_IMAGE
from scripts.constants import get_docker_image_prefix, project_env
from scripts.paths import BACKUP_DIR
from scripts.printing import print_status


def _ssh(command: str, *, stdin: IO[str] | None = None, stdout: IO[str] | None = None) -> None:
    subprocess.run(['ssh', f'root@{project_env.project_domain}', command], check=True, stdin=stdin, stdout=stdout)


def _require(key: str) -> str:
    value = os.environ.get(key)
    if not value:
        raise SystemExit(f'{key} is not set in environment/env.prod')
    return value


def _psql_container(tool: str, *args: str) -> str:
    """A postgres client container on the server, connected to the production database."""
    return (
        f'docker run --rm -i -e PGPASSWORD={shlex.quote(_require("DATABASE_PASSWORD"))} {POSTGRES_IMAGE} '
        f'{tool} -U {shlex.quote(_require("DATABASE_USER"))} -h {shlex.quote(_require("DATABASE_HOST"))} '
        f'-p {shlex.quote(_require("DATABASE_PORT"))} ' + ' '.join(shlex.quote(a) for a in args)
    )


def dumpprod(_args: list[str]) -> None:
    os.makedirs(BACKUP_DIR, exist_ok=True)
    path = os.path.join(BACKUP_DIR, f'{project_env.project_name}.sql')
    print_status(f'Dumping the production database to {path}')
    with open(path, 'w', encoding='utf-8') as out:
        _ssh(_psql_container('pg_dump', _require('DATABASE_NAME')), stdout=out)


def restoreprod(args: list[str]) -> None:
    if len(args) != 2:
        raise SystemExit('usage: dc restoreprod <database> <dump.sql>')
    database, path = args
    print_status(f'Restoring {path} into the production database {database}')
    with open(path, encoding='utf-8') as dump:
        _ssh(_psql_container('psql', '-d', database), stdin=dump)


def _run_in_image(component: str, command: Sequence[str]) -> None:
    app = project_env.prod_app_path
    _ssh(
        f'docker run --rm -i --env-file={app}/env.base --env-file={app}/env '
        f'{get_docker_image_prefix()}-{component} ' + ' '.join(shlex.quote(c) for c in command)
    )


def djangoprod(args: list[str]) -> None:
    _run_in_image('django', ['python', 'manage.py', *args])


def fastapiprod(args: list[str]) -> None:
    _run_in_image('fastapi', args)


def logsprod(args: list[str]) -> None:
    if len(args) != 1:
        raise SystemExit('usage: dc logsprod <service>')
    name = project_env.project_name
    _ssh(f'docker service logs {name}_{name}-{shlex.quote(args[0])}')
