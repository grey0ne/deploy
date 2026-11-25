from collections.abc import Callable
from typing import Any
import time

from scripts.http_request import request
from scripts.dns_records import find_zone, plan_a_records, record_name
from scripts.constants import (
    project_env, DO_API_DOMAIN, DROPLETS_URL, DROPLET_TAGS, PG_SIZE, PG_VERSION, PG_NODES_NUM,
    STATUS_CHECK_INTERVAL, get_do_headers
)


class DOException(Exception):
    pass



def do_get_request(url: str) -> dict[str, Any]:
    response = request(url=url, method="GET", headers=get_do_headers())
    if response.status != 200:
        raise DOException(f"DO request failed. URL {url} Status code: {response.status} Body: {response.body}")
    return response.json()


def do_post_request(url: str, data: dict[str, Any] = {}) -> dict[str, Any]:
    response = request(url=url, method="POST", headers=get_do_headers(), data=data)
    if response.status not in [202, 201, 200]:
        raise DOException(f"DO request failed. URL {url} Status code: {response.status} Body: {response.body}")
    return response.json()

def do_put_request(url: str, data: dict[str, Any] = {}) -> dict[str, Any]:
    response = request(url=url, method="PUT", headers=get_do_headers(), data=data)
    if response.status not in [204, 202, 201, 200]:
        raise DOException(f"DO request failed. URL {url} Status code: {response.status} Body: {response.body}")
    return response.json()

def get_existing_project(name: str) -> dict[str, Any] | None:
    url = f"{DO_API_DOMAIN}/v2/projects?name={name}"
    data = do_get_request(url)
    projects = data["projects"]
    if len(projects) > 0:
        return projects[0]

def get_or_create_project(name: str, description: str):
    existing_project = get_existing_project(name)
    if existing_project is not None:
        return existing_project
    url = f"{DO_API_DOMAIN}/v2/projects"
    project_data: dict[str, Any] = {
        "name": name,
        "description": description,
        "purpose": "Web Application",
        "environment": "Production"
    }
    response = do_post_request(url, project_data)
    return response["project"]

def list_droplets() -> list[dict[str, Any]]:
    """All droplets of the account (the API pages them)."""
    droplets: list[dict[str, Any]] = []
    url: str | None = f"{DROPLETS_URL}?per_page=200"
    while url:
        data = do_get_request(url)
        droplets.extend(data["droplets"])
        url = data.get("links", {}).get("pages", {}).get("next")
    return droplets


def get_existing_droplet(name: str) -> dict[str, Any] | None:
    url = f"{DROPLETS_URL}?name={name}"
    data = do_get_request(url)
    droplets = data["droplets"]
    if len(droplets) > 0:
        return droplets[0]


def create_droplet(name: str, project_id: str) -> dict[str, Any]:
    url = DROPLETS_URL
    data: dict[str, Any] = {
        "name": name,
        "region": project_env.do_region,
        "size": project_env.do_droplet_size,
        "image": project_env.do_droplet_os_image,
        "ssh_keys": [project_env.do_ssh_fingerprint],
        "backups": False,
        "monitoring": True,
        "tags": DROPLET_TAGS,
        "ipv6": False,
        "with_droplet_agent": True,
        "project_id": project_id
    }
    response = do_post_request(url, data)
    return response['droplet']


def get_public_address(droplet_data: dict[str, Any]) -> str | None:
    networks = droplet_data["networks"]
    for network in networks["v4"]:
        if network["type"] == "public":
            return network["ip_address"]


def get_or_create_droplet(name: str, project_id: str) -> dict[str, Any]:
    existing_droplet = get_existing_droplet(name)
    if existing_droplet is not None:
        print(f"Droplet {name} already exists")
        return existing_droplet
    print(f"Creating droplet {name}. Usually takes about 15 seconds to activate")
    droplet_data = create_droplet(name, project_id)
    droplet_status = droplet_data['status']
    public_address = get_public_address(droplet_data)
    while droplet_status != "active" or public_address is None:
        print(f"Droplet {name} Status {droplet_status} Address {public_address} Waiting")
        time.sleep(STATUS_CHECK_INTERVAL)
        updated_droplet_data = get_existing_droplet(name)
        droplet_status = updated_droplet_data and updated_droplet_data['status']
        if updated_droplet_data:
            public_address = get_public_address(updated_droplet_data)
    return droplet_data


def get_existing_pg_cluster(name: str):
    url = f"{DO_API_DOMAIN}/v2/databases?name={name}"
    data = do_get_request(url)
    databases = data["databases"]
    if databases is not None and len(databases) > 0:
        return databases[0]

def create_pg_cluster(name: str, project_id: str):
    url = f"{DO_API_DOMAIN}/v2/databases"
    pg_cluster_data: dict[str, Any] = {
        "name": name,
        "engine": "pg",
        "num_nodes": PG_NODES_NUM,
        "region": project_env.do_region,
        "size": PG_SIZE,
        "version": PG_VERSION,
        "tags": ["auto-created"],
        "project_id": project_id
    }
    response = do_post_request(url, pg_cluster_data)
    return response['database']

def get_or_create_pg_cluster(name: str, project_id: str):
    existing_cluster = get_existing_pg_cluster(name)
    if existing_cluster is not None:
        return existing_cluster
    return create_pg_cluster(name, project_id)

def get_pg_user(cluster_id: str, username: str):
    url = f"{DO_API_DOMAIN}/v2/databases/{cluster_id}/users?name={username}"
    data = do_get_request(url)
    users = data["users"]
    if users is not None:
        for user in users:
            if user["name"] == username:
                return user

def get_or_create_pg_user(cluster_id: str, username: str):
    existing_user = get_pg_user(cluster_id, username)
    if existing_user is not None:
        return existing_user
    url = f"{DO_API_DOMAIN}/v2/databases/{cluster_id}/users"
    pg_user_data = {"name": username}
    result = do_post_request(url, pg_user_data)
    return result["user"]

def get_existing_pg_database(cluster_id: str, db_name: str):
    url = f'{DO_API_DOMAIN}/v2/databases/{cluster_id}/dbs?name={db_name}'
    response = do_get_request(url)
    dbs = response['dbs']
    if dbs is not None:
        for db in dbs:
            if db['name'] == db_name:
                return db

def create_pg_database(cluster_id: str, db_name: str):
    url = f'{DO_API_DOMAIN}/v2/databases/{cluster_id}/dbs'
    db_data = {
        'name': db_name
    }
    response = do_post_request(url, db_data)
    return response['db']


def get_or_create_pg_database(cluster_id: str, db_name: str):
    existing_db = get_existing_pg_database(cluster_id, db_name)
    if existing_db is not None:
        return existing_db
    return create_pg_database(cluster_id, db_name)

def get_pg_firewall(cluster_id: str) -> list[Any]:
    url = f"{DO_API_DOMAIN}/v2/databases/{cluster_id}/firewall"
    response = do_get_request(url)
    return response['rules']

def update_pg_firewall(cluster_id: str, rules: list[Any]):
    url = f"{DO_API_DOMAIN}/v2/databases/{cluster_id}/firewall"
    firewall_data = {
        "rules": rules
    }
    do_put_request(url, firewall_data)


def add_pg_firewall_rule(cluster_id: str, droplet_id: str):
    existing_rules = get_pg_firewall(cluster_id)
    for rule in existing_rules:
        if rule['type'] == 'droplet' and rule['value'] == str(droplet_id):
            print(f'Firewall rule for droplet {droplet_id} already exists')
            return
    existing_rules.append({
        "type": "droplet",
        "value": str(droplet_id)
    })
    update_pg_firewall(cluster_id, existing_rules)
    print(f'Updating firewall data for PG cluster {cluster_id} to allow droplet {droplet_id}')


def list_domain_zones() -> list[str]:
    """Names of the DNS zones of the account (the API pages them)."""
    zones: list[str] = []
    url: str | None = f"{DO_API_DOMAIN}/v2/domains?per_page=200"
    while url:
        data = do_get_request(url)
        zones.extend(zone["name"] for zone in data["domains"])
        url = data.get("links", {}).get("pages", {}).get("next")
    return zones


def get_a_records(zone: str, domain: str) -> list[dict[str, Any]]:
    """A records of exactly this domain (fully qualified) in the zone."""
    url = f"{DO_API_DOMAIN}/v2/domains/{zone}/records?type=A&name={domain}&per_page=200"
    return do_get_request(url)["domain_records"]


def do_delete_request(url: str) -> None:
    response = request(url=url, method="DELETE", headers=get_do_headers())
    if response.status not in [204, 202, 200]:
        raise DOException(f"DO request failed. URL {url} Status code: {response.status} Body: {response.body}")


def _a_record_data(name: str, address: str) -> dict[str, Any]:
    return {"type": "A", "name": name, "data": address, "ttl": 1800}


def ensure_a_record(domain: str, address: str, ask: Callable[[str], str] = input) -> bool:
    """Point the A record of the domain to the address in its DigitalOcean zone.

    Only records of this exact name are touched; repointing a domain that resolves elsewhere is
    confirmed first. False when the zone is not in the account or the user declined.
    """
    zone = find_zone(domain, list_domain_zones())
    if zone is None:
        print(f"DNS zone of {domain} is not in this DigitalOcean account: create the A record "
              f"{domain} -> {address} at its DNS provider")
        return False
    name = record_name(domain, zone)
    records_url = f"{DO_API_DOMAIN}/v2/domains/{zone}/records"
    plan = plan_a_records(get_a_records(zone, domain), address)
    if plan.action == 'keep':
        print(f"DNS A record {domain} -> {address} is up to date")
        return True
    if plan.action == 'create':
        do_post_request(records_url, _a_record_data(name, address))
        print(f"DNS A record created: {domain} -> {address} (zone {zone})")
        return True
    current = ', '.join(sorted({plan.update["data"], *(record["data"] for record in plan.delete)}))
    try:
        answer = ask(f"{domain} points to {current}. Point it to {address}? [y/N]: ").strip().lower()
    except EOFError:
        answer = ''
    if answer not in ('y', 'yes'):
        print(f"DNS A record of {domain} is not changed")
        return False
    do_put_request(f"{records_url}/{plan.update['id']}", _a_record_data(name, address))
    for record in plan.delete:
        do_delete_request(f"{records_url}/{record['id']}")
    print(f"DNS A record updated: {domain} -> {address} (zone {zone})")
    return True
