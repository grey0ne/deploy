from dataclasses import dataclass
import os
from scripts.printing import print_status
from scripts.do_init import init_do_infra
from scripts.commands import (
    setup_balancer, update_dev_nginx, production_setup, create_s3_backup,
    restore_s3_backup, create_s3_dev_bucket
)
from scripts.deploy_production import deploy_production
from scripts.compose.generate import generate_dev_compose_files
from scripts.constants import update_environment, PROD_ENV_FILE, DEV_ENV_FILE
from typing import Callable, Literal


@dataclass(kw_only=True, slots=True, frozen=True)
class Command:
    keywords: list[str]
    description: str
    func: Callable[[], None]
    env: Literal['dev', 'prod']

def list_commands():
    print_status("Available commands:")
    for command_data in COMMANDS:
        print(f"- {command_data.keywords[0]}: {command_data.description}")

def load_env(env_file: str):
    with open(env_file, 'r') as file:
        for line in file:
            if line.startswith('#') or not line.strip():
                continue
            key, value = line.strip().split('=', 1)
            os.environ[key] = value
    update_environment()


COMMANDS: list[Command] = [
    Command(
        keywords=["prod_setup", "production_setup", "initprod"],
        description="Set up the production environment",
        func=production_setup,
        env='prod'
    ),
    Command(
        keywords=["initinfra",],
        description="Initialize the infrastructure on Digital Ocean",
        func=init_do_infra,
        env='prod'
    ),
    Command(
        keywords=["setup_balancer", "deploybalancer"],
        description="Set up the load balancer on Digital Ocean",
        func=setup_balancer,
        env='prod'
    ),
    Command(
        keywords=["generate_compose",],
        description="Generate docker compose files from deploy.json",
        func=generate_dev_compose_files,
        env='dev'
    ),
    Command(
        keywords=["update_dev_nginx",],
        description="Update the dev nginx balancer configuration",
        func=update_dev_nginx,
        env='dev'
    ),
    Command(
        keywords=["deploy", "deployprod"],
        description="Build and deploy the production environment",
        func=deploy_production,
        env='prod'
    ),
    Command(
        keywords=["s3backupdev",],
        description="Download all files from Dev S3 to local directory",
        func=lambda: create_s3_backup(DEV_ENV_FILE),
        env='dev'
    ),
    Command(
        keywords=["s3restoredev",],
        description="Restores files from local backup to Dev S3",
        func=lambda: restore_s3_backup(DEV_ENV_FILE),
        env='dev'
    ),
    Command(
        keywords=["s3backupprod",],
        description="Download all files from Production S3 to local directory",
        func=lambda: create_s3_backup(PROD_ENV_FILE),
        env='prod'
    ),
    Command(
        keywords=["s3restoreprod",],
        description="Restores files from local backup to Production S3",
        func=lambda: restore_s3_backup(PROD_ENV_FILE),
        env='prod'
    ),
    Command(
        keywords=["s3createdevbucket",],
        description="Create S3 bucket for dev environment",
        func=create_s3_dev_bucket,
        env='dev'
    )
]

COMMANDS_DICT: dict[str, Command] = {}

for command_data in COMMANDS:
    for keyword in command_data.keywords:
        if keyword in COMMANDS_DICT:
            raise ValueError(f"Duplicate command keyword. Improper configuration: {keyword}")
        COMMANDS_DICT[keyword] = command_data

def execute_command(command: str):
    if command not in COMMANDS_DICT:
        print_status(f"Command '{command}' not found.")
        list_commands()
        return
    command_object = COMMANDS_DICT[command]
    if command_object.env == 'prod':
        print_status("Loading production environment variables")
        load_env(PROD_ENV_FILE)
    else:
        print_status("Loading development environment variables")
        load_env(DEV_ENV_FILE)
    print_status(f"Executing command '{command_object.description}'")
    command_object.func()