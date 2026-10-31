import os

from scripts.http_request import request
from scripts.helpers import save_env_option, run_command
from scripts.printing import print_status, print_warning
from scripts.constants import project_env, VERSION_FILE

INITIAL_VERSION = '0.0.0'

def sentry_release(version: str):
    if not project_env.sentry_release_token:
        print_warning('SENTRY_RELEASE_TOKEN is not set. Skipping Sentry release.')
        return
    print_status(f'Sending verion {version} to Sentry. Projects {project_env.sentry_projects.replace(",", ", ")}')
    headers = {
        'Authorization': f'Bearer {project_env.sentry_release_token}',
    }
    request(
        url=project_env.sentry_url,
        headers=headers,
        data={
            'version': version,
            'projects': project_env.sentry_projects.split(',')
        },
        method='POST',
    )

def increment_version(version: str) -> str:
    last_digit = int(version.split('.')[-1])
    return '.'.join(version.split('.')[:-1] + [str(last_digit + 1)])

def read_version():
    if not os.path.exists(VERSION_FILE):
        print_warning(f'Version file {VERSION_FILE} not found. Creating it with version {INITIAL_VERSION}')
        with open(VERSION_FILE, 'w') as f:
            f.write(INITIAL_VERSION)
        return INITIAL_VERSION
    with open(VERSION_FILE, 'r') as f:
        return f.read().strip()

def save_version(version: str):
    with open(VERSION_FILE, 'w') as f:
        f.write(version)
    save_env_option('PROJECT_VERSION', str(version), create=True)
    save_env_option('NEXT_PUBLIC_VERSION', str(version), create=True)

def commit_version(version: str):
    run_command(f'git add environment/version && git commit environment/version -m "Version {version}"')

def update_version():
    current_version = read_version()
    next_version = increment_version(current_version)
    save_version(next_version)
    print_status(f'Version of {project_env.project_name} updated to {next_version}')
    return next_version

def release_version(version: str):
    commit_version(version)
    print_status(f'Releasing version {version} of {project_env.project_name}')
    sentry_release(version)