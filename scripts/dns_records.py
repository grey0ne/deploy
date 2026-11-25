"""DigitalOcean DNS: the zone of a domain and what to do with its A records."""
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Record = Mapping[str, Any]


def find_zone(domain: str, zones: Iterable[str]) -> str | None:
    """The most specific zone of the account that holds the domain: ap.grey.one → grey.one."""
    domain = domain.lower().rstrip('.')
    matching = [
        zone for zone in (z.lower().rstrip('.') for z in zones)
        if domain == zone or domain.endswith(f'.{zone}')
    ]
    return max(matching, key=len, default=None)


def record_name(domain: str, zone: str) -> str:
    """Name of the record inside the zone: "@" for the zone itself, "ap" for ap.grey.one."""
    domain = domain.lower().rstrip('.')
    if domain == zone:
        return '@'
    return domain.removesuffix(f'.{zone}')


@dataclass(frozen=True, slots=True)
class Plan:
    """keep: the record already points to the address; create: there is none; replace: update
    the first record to the address and delete the others (they point elsewhere)."""
    action: Literal['keep', 'create', 'replace']
    update: Record | None = None
    delete: Sequence[Record] = field(default_factory=tuple)


def plan_a_records(records: Sequence[Record], address: str) -> Plan:
    if not records:
        return Plan('create')
    if all(record['data'] == address for record in records):
        return Plan('keep')
    # Prefer updating a record that already has the address, so nothing changes for it
    ordered = sorted(records, key=lambda record: record['data'] != address)
    return Plan('replace', update=ordered[0], delete=tuple(ordered[1:]))
