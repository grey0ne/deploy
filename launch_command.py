import sys

from scripts.commands_list import execute_command
from scripts.deploy_config import DeployConfigError, report_deploy_config_error

command = sys.argv[1]

try:
    execute_command(command)
except DeployConfigError as exc:
    report_deploy_config_error(exc)
    sys.exit(1)
