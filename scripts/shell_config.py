"""Print deploy.json component settings as shell assignments for `dc` (eval'd)."""
import shlex
import sys

from scripts.compose.services import backend_internal_url
from scripts.deploy_config import DeployConfigError, get_deploy_config


def main() -> None:
    try:
        config = get_deploy_config()
    except DeployConfigError as exc:
        # stdout is eval'd by dc: the message goes to stderr, stdout only stops the script.
        print(f'Error: {exc}', file=sys.stderr)
        print('exit 1')
        sys.exit(0)
    yes_no = {True: 'yes', False: 'no'}
    openapi_url = (
        f'{backend_internal_url(config.nextjs.backend)}/api/openapi.json' if config.nextjs.backend else ''
    )
    values = {
        'DJANGO_ENABLED': yes_no[config.django.enabled],
        'FASTAPI_ENABLED': yes_no[config.fastapi.enabled],
        'NEXTJS_ENABLED': yes_no[config.nextjs.enabled],
        'COMPONENTS': ' '.join(config.components),
        'FASTAPI_MIGRATE_COMMAND': config.fastapi.migrate_command,
        'OPENAPI_URL': openapi_url,
    }
    for key, value in values.items():
        print(f'{key}={shlex.quote(value)}')


if __name__ == '__main__':
    main()
