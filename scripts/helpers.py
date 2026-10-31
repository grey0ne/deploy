import subprocess
import fileinput

from scripts.constants import project_env, PROD_ENV_FILE


def save_env_option(option_name: str, value: str, env_file: str = PROD_ENV_FILE, create: bool = False):
    option_found = False
    with fileinput.input(files=(env_file, ), encoding="utf-8", inplace=True) as f:
        for line in f:
            if f'{option_name}=' in line:
                result = f'{option_name}={value}\n'
                option_found=True
            else:
                result = line
            print(result, end='')
        if not option_found:
            if create:
                print(f'{option_name}={value}\n', end='')
            else:
                raise Exception('Option not found')

def run_command(command: str):
    subprocess.run(command, shell=True, check=True)

def run_remote_commands(commands: list[str]):
    ssh_command = ['ssh', f'root@{project_env.project_domain}', "bash -s"]
    command = "\n".join(commands)
    if project_env.verbose:
        for cmd in commands:
            print(f"Running command: {cmd}")
    # Output is not captured so progress of long remote commands is visible
    subprocess.run(ssh_command, input=command, encoding='utf8')

def remote_command_succeeds(command: str) -> bool:
    ssh_command = ['ssh', f'root@{project_env.project_domain}', command]
    return subprocess.run(ssh_command, stdout=subprocess.DEVNULL).returncode == 0

def envsubst(from_file: str, to_file: str, variables: list[str] = []):
    if variables:
        variables_str = f"'{",".join([f'${var}' for var in variables])}'"
    else:
        variables_str = ""
    run_command(f'envsubst {variables_str} < {from_file} > {to_file}')
