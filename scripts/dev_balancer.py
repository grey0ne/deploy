"""Dev balancer prerequisites: certificates, /etc/hosts entries and the project's proxy config.

`dc up` calls ensure_dev_balancer(): it repairs only what is missing or outdated (so sudo is
asked only when a certificate must be generated/trusted or a hosts entry added): certificates
are regenerated when they expire soon or do not cover the domain. The proxy config is written
only when the rendered config differs, after `proxy -check` validated it together with every
other project's config. The proxy picks up changed configs and certificates by itself.
"""
import os
import shutil
import subprocess
import tempfile

from scripts.compose.services import DEV_PROXY_IMAGE, PROXY_DIR
from scripts import proxy_version

# container_name of the dev balancer (compose/services.py build_dev_balancer_compose)
DEV_PROXY_CONTAINER = 'proxy'
from scripts.constants import project_env
from scripts.deploy_config import get_deploy_config
from scripts.helpers import run_command
from scripts.printing import print_status
from scripts.proxy.configuration import DEV_CERTS_MOUNT, config_file_name, dev_sites, render_sites

DEVNET = 'devnet'
HOSTS_FILE = '/etc/hosts'
SYSTEM_KEYCHAIN = '/Library/Keychains/System.keychain'
# Regenerate dev certificates expiring sooner than this
CERT_RENEW_BEFORE_DAYS = 14
# macOS rejects certificates from user-trusted roots valid for more than 825 days.
CERT_DAYS = 825
# The system LibreSSL on macOS; Homebrew's openssl is not required.
OPENSSL = '/usr/bin/openssl' if os.path.exists('/usr/bin/openssl') else 'openssl'


class ProxyConfigError(Exception):
    """The rendered config fails `proxy -check` together with the other projects' configs."""


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
    """Self-signed certificate for ``domain`` (trusted separately in the system keychain)."""
    print_status(f'Generating cert {cert_name} for {domain}')
    cert, key = _cert_paths(cert_name)
    os.makedirs(os.path.dirname(cert), exist_ok=True)
    openssl_config = (
        f'[dn]\nCN={domain}\n[req]\ndistinguished_name = dn\n'
        f'[EXT]\nsubjectAltName=DNS.1:{domain}\nkeyUsage=digitalSignature\nextendedKeyUsage=serverAuth\n'
    )
    with tempfile.NamedTemporaryFile('w', suffix='.cnf', encoding='utf-8') as config_file:
        config_file.write(openssl_config)
        config_file.flush()
        subprocess.run(
            [
                OPENSSL, 'req', '-x509', '-out', cert, '-keyout', key,
                '-newkey', 'rsa:2048', '-nodes', '-sha256', '-days', str(CERT_DAYS),
                '-subj', f'/CN={domain}', '-extensions', 'EXT', '-config', config_file.name,
            ],
            check=True,
        )
    print_status(f'New certificate: {cert}, key: {key}')


def add_cert_to_trusted(cert_path: str) -> None:
    print_status(f'Adding cert to trusted {cert_path}')
    run_command(f'sudo security add-trusted-cert -d -r trustRoot -k {SYSTEM_KEYCHAIN} {cert_path}')


def _cert_sha1(cert_path: str) -> str:
    output = subprocess.run(
        ['openssl', 'x509', '-in', cert_path, '-noout', '-fingerprint', '-sha1'],
        capture_output=True, text=True, check=True,
    ).stdout
    return output.strip().split('=', 1)[1].replace(':', '').upper()


def remove_stale_trusted_certs(domain: str, cert_path: str) -> None:
    """Delete certificates for `domain` from the system keychain except the current one.

    Every regeneration adds a new self-signed certificate; old ones would pile up otherwise.
    """
    if shutil.which('security') is None:
        return
    current = _cert_sha1(cert_path)
    listing = subprocess.run(
        ['security', 'find-certificate', '-a', '-c', domain, '-Z', SYSTEM_KEYCHAIN],
        capture_output=True, text=True,
    ).stdout
    stale, sha1 = [], None
    for line in listing.splitlines():
        line = line.strip()
        if line.startswith('SHA-1 hash:'):
            sha1 = line.split(':', 1)[1].strip()
        # -c matches substrings (media.<domain>), so compare the label exactly
        elif line == f'"labl"<blob>="{domain}"' and sha1 and sha1 != current:
            stale.append(sha1)
    for sha1 in dict.fromkeys(stale):
        print_status(f'Removing outdated cert for {domain} from keychain ({sha1})')
        run_command(f'sudo security delete-certificate -Z {sha1} {SYSTEM_KEYCHAIN}')


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


def cert_outdated_reason(cert_path: str, domain: str) -> str | None:
    """Why the certificate must be regenerated, or None when it is fine."""
    expires_soon = subprocess.run(
        ['openssl', 'x509', '-in', cert_path, '-noout', '-checkend', str(CERT_RENEW_BEFORE_DAYS * 24 * 3600)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    if expires_soon.returncode != 0:
        return f'expires in less than {CERT_RENEW_BEFORE_DAYS} days or has expired'
    text = subprocess.run(
        ['openssl', 'x509', '-in', cert_path, '-noout', '-text'], capture_output=True, text=True,
    ).stdout
    if f'DNS:{domain}' not in text:
        return f'does not cover {domain}'
    return None


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
    print_status(f'Adding {domain} to {HOSTS_FILE}')
    subprocess.run(
        ['sudo', 'tee', '-a', HOSTS_FILE], input=f'127.0.0.1\t{domain}\n', text=True,
        stdout=subprocess.DEVNULL, check=True,
    )


def generate_dev_certs() -> None:
    """Regenerate and trust every dev certificate (`dc gencerts`)."""
    for cert_name, domain in dev_cert_targets():
        cert = _cert_paths(cert_name)[0]
        gen_cert(cert_name, domain)
        add_cert_to_trusted(cert)
        remove_stale_trusted_certs(domain, cert)
        if not hosts_has(domain):
            update_hosts(domain)


def ensure_dev_certs() -> bool:
    """Generate, trust and map only what is missing or outdated.

    The proxy reloads changed certificate files by itself.
    """
    for cert_name, domain in dev_cert_targets():
        cert, key = _cert_paths(cert_name)
        if not (os.path.isfile(cert) and os.path.isfile(key)):
            reason = 'is missing'
        else:
            reason = cert_outdated_reason(cert, domain)
        if reason:
            print_status(f'Cert {cert_name} {reason}')
            gen_cert(cert_name, domain)
            add_cert_to_trusted(cert)
            remove_stale_trusted_certs(domain, cert)
        elif not cert_is_trusted(cert):
            add_cert_to_trusted(cert)
        if not hosts_has(domain):
            update_hosts(domain)


# ── proxy config ──────────────────────────────────────────────────────────────


def render_dev_proxy_config() -> str:
    return render_sites(dev_sites())


def _config_path() -> str:
    return os.path.join(project_env.proxy_config_dir, config_file_name())


def _config_changed(content: str) -> bool:
    try:
        with open(_config_path(), encoding='utf-8') as f:
            return f.read() != content
    except OSError:
        return True


def ensure_proxy_image() -> None:
    """The proxy image of this deploy version, needed for `proxy -check` before `up`."""
    exists = subprocess.run(
        ['docker', 'image', 'inspect', DEV_PROXY_IMAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if exists.returncode != 0:
        print_status(f'Building {DEV_PROXY_IMAGE}')
        subprocess.run(['docker', 'build', '-t', DEV_PROXY_IMAGE, PROXY_DIR], check=True)


def running_dev_proxy_image() -> str | None:
    """Image of the dev balancer container; None when it does not exist."""
    result = subprocess.run(
        ['docker', 'inspect', DEV_PROXY_CONTAINER, '--format', '{{.Config.Image}}'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def dev_proxy_is_newer() -> bool:
    """The running dev balancer is newer than the proxy of this deploy version (keep it)."""
    return proxy_version.is_newer(running_dev_proxy_image(), DEV_PROXY_IMAGE)


def validate_dev_proxy(content: str) -> None:
    """`proxy -check` on all projects' configs with ours replaced, using the balancer's mounts.

    Checked by the proxy that will read the config: a newer running balancer may know more fields.
    """
    check_image = proxy_version.check_image(running_dev_proxy_image(), DEV_PROXY_IMAGE)
    if check_image == DEV_PROXY_IMAGE:
        ensure_proxy_image()
    with tempfile.TemporaryDirectory() as tmp:
        if os.path.isdir(project_env.proxy_config_dir):
            for file_name in os.listdir(project_env.proxy_config_dir):
                if file_name.endswith(('.yaml', '.yml')) and not file_name.startswith('.'):
                    shutil.copy(os.path.join(project_env.proxy_config_dir, file_name), tmp)
        with open(os.path.join(tmp, config_file_name()), 'w', encoding='utf-8') as f:
            f.write(content)
        result = subprocess.run(
            [
                'docker', 'run', '--rm',
                '-v', f'{tmp}:/config:ro',
                '-v', f'{project_env.ssl_certs_dir}:{DEV_CERTS_MOUNT}:ro',
                check_image, '-check', '-config', '/config',
            ],
            capture_output=True,
            text=True,
        )
    if result.returncode != 0:
        raise ProxyConfigError('; '.join(result.stderr.strip().splitlines()) or 'proxy -check failed')


def write_dev_proxy(content: str) -> None:
    """Written next to the target and renamed: the proxy never reads a half-written file."""
    os.makedirs(project_env.proxy_config_dir, exist_ok=True)
    path = _config_path()
    print_status(f'Writing proxy config {path}')
    tmp_path = os.path.join(project_env.proxy_config_dir, f'.{config_file_name()}.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as f:
        f.write(content)
    os.replace(tmp_path, path)


def update_dev_proxy() -> None:
    """Validate and write the project's balancer config (`dc updateproxy`)."""
    content = render_dev_proxy_config()
    validate_dev_proxy(content)
    write_dev_proxy(content)


def ensure_devnet() -> None:
    exists = subprocess.run(
        ['docker', 'network', 'inspect', DEVNET], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    if exists.returncode != 0:
        print_status(f'Creating docker network {DEVNET}')
        run_command(f'docker network create {DEVNET}')


def ensure_dev_balancer() -> None:
    """Prerequisites for `dc up`.

    Raises ProxyConfigError (nothing written) when the new proxy config is invalid.
    """
    ensure_devnet()
    ensure_dev_certs()
    content = render_dev_proxy_config()
    if not _config_changed(content):
        print_status('Proxy config is up to date')
        return
    print_status(f'Proxy config changed: {config_file_name()}')
    validate_dev_proxy(content)
    write_dev_proxy(content)
