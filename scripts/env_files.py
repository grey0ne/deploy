"""Environment files of the project, loaded once per `dc` run.

dev:  environment/env.base, environment/env.dev, .env (optional)
prod: environment/env.base, environment/env.prod, .env (optional)

Later files override earlier ones; variables already set in the shell are overridden too (as
`source` did). The syntax is the shell subset the files use: comments, blank lines, optional
`export`, KEY=value with '…' (literal) or "…" / unquoted values, where $NAME and ${NAME} are
expanded. "~" is not expanded here (constants.py expands the path settings).
"""
import os
import re
from collections.abc import Mapping
from typing import Literal

from scripts.paths import BASE_ENV_FILE, DEV_ENV_FILE, LOCAL_ENV_FILE, PROD_ENV_FILE

EnvKind = Literal['dev', 'prod']

_KEY = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')
_REFERENCE = re.compile(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))')


class EnvFileError(Exception):
    """An env file is missing or cannot be parsed."""


def _expand(value: str, defined: Mapping[str, str]) -> str:
    return _REFERENCE.sub(lambda m: defined.get(m.group(1) or m.group(2), ''), value)


def _parse_value(raw: str, defined: Mapping[str, str], where: str) -> str:
    if raw.startswith("'"):
        end = raw.find("'", 1)
        if end == -1:
            raise EnvFileError(f'{where}: unterminated single quote')
        return raw[1:end]
    if raw.startswith('"'):
        chars: list[str] = []
        i = 1
        while i < len(raw):
            char = raw[i]
            if char == '\\' and i + 1 < len(raw) and raw[i + 1] in '"\\$`':
                chars.append(raw[i + 1])
                i += 2
                continue
            if char == '"':
                return _expand(''.join(chars), defined)
            chars.append(char)
            i += 1
        raise EnvFileError(f'{where}: unterminated double quote')
    # Unquoted: a " #" starts a comment.
    value = re.split(r'\s+#', raw, maxsplit=1)[0].strip()
    return _expand(value, defined)


def parse_env_file(path: str, defined: Mapping[str, str] | None = None) -> dict[str, str]:
    """Variables of one file; ``defined`` resolves references to earlier variables."""
    known = dict(defined or {})
    values: dict[str, str] = {}
    with open(path, encoding='utf-8') as f:
        for number, line in enumerate(f, start=1):
            text = line.strip()
            if not text or text.startswith('#'):
                continue
            if text.startswith('export '):
                text = text[len('export '):].lstrip()
            key, sep, raw = text.partition('=')
            where = f'{path}:{number}'
            if not sep or not _KEY.match(key):
                raise EnvFileError(f'{where}: expected KEY=value, got {text[:60]!r}')
            value = _parse_value(raw.strip(), known, where)
            values[key] = value
            known[key] = value
    return values


def env_files(kind: EnvKind) -> list[str]:
    files = [BASE_ENV_FILE, DEV_ENV_FILE if kind == 'dev' else PROD_ENV_FILE]
    if os.path.isfile(LOCAL_ENV_FILE):
        files.append(LOCAL_ENV_FILE)
    return files


def read_environment(kind: EnvKind, base: Mapping[str, str]) -> dict[str, str]:
    """Variables of the ``kind`` environment layered over ``base`` (the process environment)."""
    values: dict[str, str] = {}
    for path in env_files(kind):
        if not os.path.isfile(path):
            raise EnvFileError(f'{path} is required for {kind} commands but was not found')
        values.update(parse_env_file(path, {**base, **values}))
    return values


def load_environment(kind: EnvKind) -> None:
    """Load the ``kind`` environment into ``os.environ`` (subprocesses inherit it)."""
    os.environ.update(read_environment(kind, os.environ))
