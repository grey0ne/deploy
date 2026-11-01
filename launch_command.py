import sys

from scripts.commands_list import execute_command
from scripts.constants import EnvConfigError
from scripts.deploy_config import DeployConfigError, report_deploy_config_error
from scripts.dev_balancer import NginxConfigError
from scripts.printing import print_error

command = sys.argv[1]

try:
    execute_command(command)
except DeployConfigError as exc:
    report_deploy_config_error(exc)
    sys.exit(1)
except EnvConfigError as exc:
    print_error(str(exc))
    sys.exit(1)
except NginxConfigError as exc:
    print_error(f'New nginx config is invalid, the balancer was not touched: {exc}')
    sys.exit(1)
