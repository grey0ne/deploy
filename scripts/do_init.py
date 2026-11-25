import time
import socket

from scripts.do_utils import (
    get_or_create_droplet, get_or_create_project, get_public_address, list_droplets,
    ensure_a_record,
    get_or_create_pg_cluster, get_existing_pg_cluster,
    get_or_create_pg_user, get_or_create_pg_database, add_pg_firewall_rule,
    DOException
)
from scripts.helpers import save_env_option
from scripts.droplet_choice import NewDroplet, choose_droplet
from scripts.constants import (
    project_env, STATUS_CHECK_INTERVAL
)


def _resolve(domain: str) -> str | None:
    try:
        return socket.gethostbyname(domain)
    except OSError:
        return None


def init_do_infra():
    REQUIRED_VARS: dict[str, str | None] = {
        'DO_TOKEN': project_env.do_token,
        'PROJECT_NAME': project_env.project_name,
        'PROJECT_DOMAIN': project_env.project_domain,
        'DATABASE_USER': project_env.pg_username
    }

    for var_name, value in REQUIRED_VARS.items():
        if not value:
            print(f"{var_name} is not set")
            return
    # Asked before anything is created: an existing droplet or a new one from the template
    choice = choose_droplet(
        list_droplets(),
        NewDroplet(
            name=project_env.do_droplet_name,
            size=project_env.do_droplet_size or '',
            region=project_env.do_region or '',
            image=project_env.do_droplet_os_image or '',
        ),
        preferred_name=project_env.do_droplet_name,
        domain_address=_resolve(project_env.project_domain),
    )
    if choice.droplet is None and not project_env.do_ssh_fingerprint:
        print("DO_KEY_FINGERPRINT is not set: a new droplet needs the SSH key to log in")
        return
    save_env_option('DO_DROPLET_NAME', choice.name)
    project_data = get_or_create_project(project_env.project_name, project_env.project_description)
    print(f"Using DO project {project_env.project_name} with id {project_data['id']}")
    droplet_data = choice.droplet or get_or_create_droplet(choice.name, project_data["id"])
    public_address = get_public_address(droplet_data)
    if public_address is None:
        raise DOException(f"Active droplet {choice.name} doesn't have public address, aborting")
    print(f"DNS record for droplet {choice.name} with IP {public_address}")
    ensure_a_record(project_env.project_domain, public_address)
    pg_cluster = get_or_create_pg_cluster(project_env.pg_cluster_name, project_data["id"])
    cluster_status = pg_cluster['status']
    cluster_id = pg_cluster['id']
    # The same DATABASE_* as the Django settings and the dump/restore commands read
    save_env_option('DATABASE_NAME', project_env.pg_db_name)
    save_env_option('DATABASE_HOST', pg_cluster['connection']['host'])
    save_env_option('DATABASE_PORT', str(pg_cluster['connection']['port']))
    while cluster_status == "creating":
        print(f"Postgres Cluster status is {cluster_status}. Cluster init could take up to 5 minutes")
        time.sleep(STATUS_CHECK_INTERVAL * 5)
        pg_cluster = get_existing_pg_cluster(project_env.pg_cluster_name)
        if pg_cluster is not None:
            cluster_status = pg_cluster['status']
    pg_user = get_or_create_pg_user(cluster_id, project_env.pg_username)
    if 'password' in pg_user:
        save_env_option('DATABASE_PASSWORD', pg_user['password'])
    get_or_create_pg_database(cluster_id, project_env.pg_db_name)
    add_pg_firewall_rule(cluster_id, droplet_data['id'])