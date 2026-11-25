"""Interactive choice of the droplet `dc initinfra` deploys to: an existing one or a new one."""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

Droplet = Mapping[str, Any]
Ask = Callable[[str], str]


@dataclass(frozen=True, slots=True, kw_only=True)
class NewDroplet:
    """Template of a new droplet (DO_SIZE, DO_REGION, DO_DROPLET_OS_IMAGE)."""
    name: str
    size: str
    region: str
    image: str


@dataclass(frozen=True, slots=True)
class Choice:
    """An existing droplet, or None: create `name` from the template."""
    droplet: Droplet | None
    name: str


def public_address(droplet: Droplet) -> str | None:
    for network in droplet.get('networks', {}).get('v4', []):
        if network.get('type') == 'public':
            return network.get('ip_address')
    return None


def describe(droplet: Droplet, domain_address: str | None) -> str:
    address = public_address(droplet) or '-'
    region = droplet.get('region', {}).get('slug', '-')
    line = f"{droplet['name']}  {address}  {region}  {droplet.get('size_slug', '-')}  {droplet.get('status', '-')}"
    if domain_address is not None and address == domain_address:
        line += '  (the project domain points here)'
    return line


def default_index(droplets: Sequence[Droplet], preferred_name: str, domain_address: str | None) -> int | None:
    """DO_DROPLET_NAME when it exists, otherwise the droplet the project domain resolves to."""
    for index, droplet in enumerate(droplets):
        if droplet['name'] == preferred_name:
            return index
    for index, droplet in enumerate(droplets):
        if domain_address is not None and public_address(droplet) == domain_address:
            return index
    return None


def _ask(ask: Ask, prompt: str) -> str:
    try:
        return ask(prompt).strip()
    except EOFError:
        raise SystemExit('Cancelled') from None


def choose_droplet(
    droplets: Sequence[Droplet],
    template: NewDroplet,
    *,
    preferred_name: str,
    domain_address: str | None,
    ask: Ask = input,
    say: Callable[[str], None] = print,
) -> Choice:
    droplets = sorted(droplets, key=lambda d: d['name'])
    default = default_index(droplets, preferred_name, domain_address)
    new_option = len(droplets) + 1
    say('Droplet to deploy to:')
    for number, droplet in enumerate(droplets, start=1):
        marker = '*' if default == number - 1 else ' '
        say(f' {marker}{number}. {describe(droplet, domain_address)}')
    say(f'  {new_option}. New droplet from the template: {template.name}  {template.size}  '
        f'{template.region}  {template.image}')
    hint = f' [{default + 1}]' if default is not None else ''
    while True:
        answer = _ask(ask, f'Choose 1-{new_option}{hint}: ')
        if not answer and default is not None:
            return Choice(droplets[default], droplets[default]['name'])
        if answer.isdigit() and 1 <= int(answer) <= len(droplets):
            droplet = droplets[int(answer) - 1]
            return Choice(droplet, droplet['name'])
        if answer == str(new_option):
            return _new_droplet(droplets, template, ask, say)
        say(f'Enter a number from 1 to {new_option}')


def _new_droplet(droplets: Sequence[Droplet], template: NewDroplet, ask: Ask, say: Callable[[str], None]) -> Choice:
    taken = {droplet['name'] for droplet in droplets}
    while True:
        name = _ask(ask, f'Name of the new droplet [{template.name}]: ') or template.name
        if name not in taken:
            break
        say(f'Droplet {name} already exists: choose it from the list or enter another name')
    confirm = _ask(ask, f'Create the paid droplet {name} ({template.size}, {template.region})? [y/N]: ')
    if confirm.lower() not in ('y', 'yes'):
        raise SystemExit('Cancelled')
    return Choice(None, name)
