from typing import Any


def _quote_string(value: str) -> str:
    if not value:
        return '""'
    if any(c in value for c in ':{}[]&*#?|-<>=!%@`"\''):
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"') + '"'
    return value


def _dump_value(value: Any, indent: int) -> str:
    prefix = '  ' * indent
    if isinstance(value, dict):
        if not value:
            return '{}'
        lines: list[str] = []
        for key, item in value.items():
            dumped = _dump_value(item, indent + 1)
            if isinstance(item, (dict, list)) and item:
                lines.append(f'{prefix}{key}:')
                lines.append(dumped)
            else:
                lines.append(f'{prefix}{key}: {dumped}')
        return '\n'.join(lines)
    if isinstance(value, list):
        if not value:
            return '[]'
        lines = []
        for item in value:
            dumped = _dump_value(item, indent + 1)
            if isinstance(item, dict) and item:
                lines.append(f'{prefix}-')
                lines.append(dumped)
            else:
                lines.append(f'{prefix}- {dumped}')
        return '\n'.join(lines)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if value is None:
        return 'null'
    if isinstance(value, (int, float)):
        return str(value)
    return _quote_string(str(value))


def dump_yaml(data: dict) -> str:
    return _dump_value(data, 0) + '\n'
