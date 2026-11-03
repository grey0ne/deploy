import os
import subprocess

from scripts.constants import project_env, PROD_ENV_FILE


def _env_line_key(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith('export '):
        stripped = stripped[len('export '):].lstrip()
    return stripped.split('=', 1)[0].strip() if '=' in stripped else ''


def save_env_option(option_name: str, value: str, env_file: str = PROD_ENV_FILE, create: bool = False):
    """Set the option in the env file and in the environment of the current run (subprocesses inherit it)."""
    with open(env_file, encoding='utf-8') as f:
        lines = f.readlines()
    option_line = f'{option_name}={value}\n'
    option_found = False
    for index, line in enumerate(lines):
        if _env_line_key(line) == option_name:
            lines[index] = option_line
            option_found = True
    if not option_found:
        if not create:
            raise Exception(f'Option {option_name} not found in {env_file}')
        if lines and not lines[-1].endswith('\n'):
            lines[-1] += '\n'
        lines.append(option_line)
    with open(env_file, 'w', encoding='utf-8') as f:
        f.writelines(lines)
    os.environ[option_name] = value

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

def remote_command_output(command: str) -> str:
    # Reuses one ssh connection for repeated calls (e.g. polling the swarm during deploy)
    ssh_command = [
        'ssh', '-o', 'ControlMaster=auto', '-o', 'ControlPath=/tmp/dc-ssh-%C', '-o', 'ControlPersist=60',
        f'root@{project_env.project_domain}', 'bash -s',
    ]
    return subprocess.run(ssh_command, input=command, encoding='utf8', stdout=subprocess.PIPE, check=True).stdout

def remote_command_succeeds(command: str) -> bool:
    ssh_command = ['ssh', f'root@{project_env.project_domain}', command]
    return subprocess.run(ssh_command, stdout=subprocess.DEVNULL).returncode == 0

def envsubst(from_file: str, to_file: str, variables: list[str] = []):
    if variables:
        variables_str = f"'{",".join([f'${var}' for var in variables])}'"
    else:
        variables_str = ""
    run_command(f'envsubst {variables_str} < {from_file} > {to_file}')
