"""Dev balancer prerequisites: certificates, /etc/hosts entries and the project's nginx config.

`dc up` calls ensure_dev_balancer(): it repairs only what is missing (so sudo is asked only
when a certificate must be generated/trusted or a hosts entry added) and updates the nginx
config only when the rendered config differs. A new config is validated with `nginx -t`
together with every other project's config before it reaches the shared balancer.
"""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

from scripts.compose.services import NGINX_IMAGE
from scripts.constants import DEPLOY_DIR, project_env
from scripts.deploy_config import get_deploy_config
from scripts.helpers import run_command
from scripts.nginx.configuration import (
    render_centrifugo_dev_nginx_conf,
    render_dev_nginx_conf,
    render_extra_dev_domain_nginx_conf,
)
from scripts.printing import print_error, print_status

DEVNET = 'devnet'
HOSTS_FILE = '/etc/hosts'
# Exit code of `ensure_dev_balancer` telling dc to restart the balancer.
NGINX_CHANGED_EXIT_CODE = 10


class NginxConfigError(Exception):
    """The rendered config fails `nginx -t` together with the other projects' configs."""


# ── Certificates and hosts ────────────────────────────────────────────────────


def dev_cert_targets() -> list[tuple[str, str]]:
    """(certificate name, domain) for every domain the project serves in dev."""
    config = get_deploy_config()
    name = project_env.project_name
    domain = project_env.project_domain
    targets = [(name, domain)]
    if config.django.enabled:
        targets.append((f'media_{name}', f'media.{domain}'))
    for extra in project_env.extra_domains:
        targets.append((f'{name}_{extra}', extra))
    if config.centrifugo_enabled:
        targets.append((f'centrifugo_{name}', f'centrifugo.{domain}'))
    return targets


def _cert_paths(cert_name: str) -> tuple[str, str]:
    base = os.path.join(project_env.ssl_certs_dir, cert_name)
    return f'{base}.crt', f'{base}.key'


def gen_cert(cert_name: str, domain: str) -> None:
    print_status(f'Generating cert {cert_name} for {domain}')
    # Pass the expanded directory: env files are parsed without a shell, so SSL_CERTS_DIR
    # in the environment may still contain a literal "~".
    run_command(
        f'SSL_CERTS_DIR="{project_env.ssl_certs_dir}" {DEPLOY_DIR}/generate_certs.sh {cert_name} {domain}'
    )


def add_cert_to_trusted(cert_path: str) -> None:
    print_status(f'Adding cert to trusted {cert_path}')
    run_command(
        f'sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain {cert_path}'
    )


def cert_is_trusted(cert_path: str) -> bool:
    if shutil.which('security') is None:
        # Not macOS: trust is managed outside these scripts.
        return True
    result = subprocess.run(
        ['security', 'verify-cert', '-c', cert_path, '-p', 'ssl'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def hosts_has(domain: str) -> bool:
    try:
        with open(HOSTS_FILE, encoding='utf-8') as f:
            for line in f:
                fields = line.split('#', 1)[0].split()
                if len(fields) >= 2 and domain in fields[1:]:
                    return True
    except OSError:
        return False
    return False


def update_hosts(domain: str) -> None:
    print_status(f'Updating hosts file with {domain}')
    run_command(f'{DEPLOY_DIR}/update_hosts.sh addhost {domain}')


def generate_dev_certs() -> None:
    """Regenerate and trust every dev certificate (`dc gencerts`)."""
    for cert_name, domain in dev_cert_targets():
        gen_cert(cert_name, domain)
        add_cert_to_trusted(_cert_paths(cert_name)[0])
        if not hosts_has(domain):
            update_hosts(domain)


def ensure_dev_certs() -> None:
    """Generate, trust and map only what is missing."""
    for cert_name, domain in dev_cert_targets():
        cert, key = _cert_paths(cert_name)
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            gen_cert(cert_name, domain)
            add_cert_to_trusted(cert)
        elif not cert_is_trusted(cert):
            add_cert_to_trusted(cert)
        if not hosts_has(domain):
            update_hosts(domain)


# ── nginx config ──────────────────────────────────────────────────────────────


def render_dev_nginx_files() -> dict[str, str]:
    """File name -> content of the project's dev balancer templates."""
    config = get_deploy_config()
    name = project_env.project_name
    # Rendered into a temp dir for comparison; the renderers' "Generating ..." lines would
    # point at that dir, so they are silenced.
    with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
        render_dev_nginx_conf(os.path.join(tmp, f'{name}.conf.template'))
        if config.centrifugo_enabled:
            render_centrifugo_dev_nginx_conf(os.path.join(tmp, f'{name}_centrifugo.conf.template'))
        for domain in project_env.extra_domains:
            render_extra_dev_domain_nginx_conf(os.path.join(tmp, f'{name}_{domain}.conf.template'), domain)
        files: dict[str, str] = {}
        for file_name in sorted(os.listdir(tmp)):
            with open(os.path.join(tmp, file_name), encoding='utf-8') as f:
                files[file_name] = f.read()
    return files


def _changed_files(files: dict[str, str]) -> list[str]:
    changed: list[str] = []
    for file_name, content in files.items():
        path = os.path.join(project_env.nginx_config_dir, file_name)
        try:
            with open(path, encoding='utf-8') as f:
                if f.read() == content:
                    continue
        except OSError:
            pass
        changed.append(file_name)
    return changed


def validate_dev_nginx(files: dict[str, str]) -> None:
    """`nginx -t` on all projects' templates with ours replaced, using the balancer's mounts."""
    with tempfile.TemporaryDirectory() as tmp:
        templates = os.path.join(tmp, 'templates')
        if os.path.isdir(project_env.nginx_config_dir):
            shutil.copytree(project_env.nginx_config_dir, templates)
        else:
            os.makedirs(templates)
        for file_name, content in files.items():
            with open(os.path.join(templates, file_name), 'w', encoding='utf-8') as f:
                f.write(content)
        result = subprocess.run(
            [
                'docker', 'run', '--rm',
                '-e', 'NGINX_ENTRYPOINT_LOCAL_RESOLVERS=yes',
                '-v', f'{templates}:/etc/nginx/templates:ro',
                '-v', f'{project_env.ssl_certs_dir}:/app/ssl:ro',
                NGINX_IMAGE, 'nginx', '-t',
            ],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        reasons = [line for line in lines if '[emerg]' in line] or lines[-1:]
        raise NginxConfigError('; '.join(reasons) or 'nginx -t failed')


def write_dev_nginx(files: dict[str, str]) -> None:
    os.makedirs(project_env.nginx_config_dir, exist_ok=True)
    for file_name, content in files.items():
        path = os.path.join(project_env.nginx_config_dir, file_name)
        print_status(f'Writing nginx config {path}')
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)


def update_dev_nginx() -> None:
    """Validate and write the project's balancer config (`dc updatenginx` restarts after)."""
    files = render_dev_nginx_files()
    validate_dev_nginx(files)
    write_dev_nginx(files)


def ensure_devnet() -> None:
    exists = subprocess.run(
        ['docker', 'network', 'inspect', DEVNET], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if exists.returncode != 0:
        print_status(f'Creating docker network {DEVNET}')
        run_command(f'docker network create {DEVNET}')


def ensure_dev_balancer() -> None:
    """Prerequisites for `dc up`; exits with NGINX_CHANGED_EXIT_CODE when the balancer must reload."""
    ensure_devnet()
    ensure_dev_certs()
    files = render_dev_nginx_files()
    changed = _changed_files(files)
    if not changed:
        print_status('Dev certificates and nginx config are up to date')
        return
    print_status(f'nginx config changed: {", ".join(changed)}')
    try:
        validate_dev_nginx(files)
    except NginxConfigError as exc:
        print_error(f'New nginx config is invalid, the balancer was not touched: {exc}')
        sys.exit(1)
    write_dev_nginx(files)
    sys.exit(NGINX_CHANGED_EXIT_CODE)
