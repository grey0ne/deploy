"""`dc initinfra`: the droplet is chosen interactively before anything is created.

Run from the project root: python3.15 -m unittest discover deploy/tests
"""
import os
import sys
import unittest
from collections.abc import Iterator
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# scripts.constants reads the project environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts import do_init  # noqa: E402
from scripts.droplet_choice import Choice, NewDroplet, choose_droplet  # noqa: E402

TEMPLATE = NewDroplet(name='demo-app', size='s-1vcpu-1gb', region='ams3', image='debian-12-x64')


def droplet(name: str, address: str, droplet_id: int = 1) -> dict[str, Any]:
    return {
        'id': droplet_id, 'name': name, 'status': 'active', 'size_slug': 's-2vcpu-4gb',
        'region': {'slug': 'ams3'},
        'networks': {'v4': [{'type': 'private', 'ip_address': '10.0.0.1'},
                            {'type': 'public', 'ip_address': address}]},
    }


DROPLETS = [droplet('web', '1.1.1.1', 1), droplet('apps', '2.2.2.2', 2)]


def answers(*values: str) -> mock.Mock:
    iterator: Iterator[str] = iter(values)

    def ask(_prompt: str) -> str:
        try:
            return next(iterator)
        except StopIteration:
            raise EOFError from None
    return mock.Mock(side_effect=ask)


def choose(*values: str, preferred: str = 'demo-app', domain: str | None = None) -> tuple[Choice, list[str]]:
    lines: list[str] = []
    choice = choose_droplet(
        DROPLETS, TEMPLATE, preferred_name=preferred, domain_address=domain, ask=answers(*values),
        say=lines.append,
    )
    return choice, lines


class ChooseDropletTest(unittest.TestCase):
    def test_lists_droplets_sorted_and_the_template(self) -> None:
        _, lines = choose('1')
        self.assertIn('1. apps  2.2.2.2  ams3  s-2vcpu-4gb  active', lines[1])
        self.assertIn('2. web  1.1.1.1', lines[2])
        self.assertIn('3. New droplet from the template: demo-app  s-1vcpu-1gb  ams3  debian-12-x64', lines[3])

    def test_existing_droplet(self) -> None:
        choice, _ = choose('2')
        self.assertEqual(choice.name, 'web')
        self.assertIsNotNone(choice.droplet)

    def test_default_is_the_droplet_of_the_project_domain(self) -> None:
        choice, lines = choose('', domain='1.1.1.1')
        self.assertEqual(choice.name, 'web')
        self.assertIn('*2. web', lines[2])
        self.assertIn('(the project domain points here)', lines[2])

    def test_default_is_do_droplet_name(self) -> None:
        choice, _ = choose('', preferred='apps', domain='1.1.1.1')
        self.assertEqual(choice.name, 'apps')

    def test_no_default_asks_again(self) -> None:
        choice, lines = choose('', '9', 'x', '1')
        self.assertEqual(choice.name, 'apps')
        self.assertEqual(lines.count('Enter a number from 1 to 3'), 3)

    def test_new_droplet_from_the_template(self) -> None:
        choice, _ = choose('3', '', 'y')
        self.assertEqual(choice, Choice(None, 'demo-app'))

    def test_new_droplet_with_another_name(self) -> None:
        choice, lines = choose('3', 'web', 'demo-2', 'yes')
        self.assertEqual(choice, Choice(None, 'demo-2'))
        self.assertIn('Droplet web already exists', lines[-1])

    def test_new_droplet_needs_confirmation(self) -> None:
        with self.assertRaises(SystemExit):
            choose('3', '', 'n')

    def test_end_of_input_cancels(self) -> None:
        with self.assertRaises(SystemExit):
            choose()


class InitInfraTest(unittest.TestCase):
    """The DigitalOcean calls of init_do_infra after the choice (all mocked)."""

    def run_init(self, choice: Choice, fingerprint: str = 'aa:bb') -> dict[str, mock.Mock]:
        env = mock.patch.multiple(
            do_init.project_env, do_token='token', pg_username='admin', do_ssh_fingerprint=fingerprint,
        )
        cluster = {'id': 'c1', 'status': 'online', 'connection': {'host': 'db.example.com', 'port': 25060}}
        mocks = {
            name: mock.patch.object(do_init, name).start()
            for name in (
                'list_droplets', 'choose_droplet', 'save_env_option', 'get_or_create_project',
                'get_or_create_droplet', 'ensure_a_record', 'get_or_create_pg_cluster',
                'get_or_create_pg_user', 'get_or_create_pg_database', 'add_pg_firewall_rule', '_resolve',
            )
        }
        self.addCleanup(mock.patch.stopall)
        env.start()
        self.addCleanup(env.stop)
        mocks['choose_droplet'].return_value = choice
        mocks['get_or_create_project'].return_value = {'id': 'p1'}
        mocks['get_or_create_droplet'].return_value = droplet(choice.name, '3.3.3.3', 3)
        mocks['get_or_create_pg_cluster'].return_value = cluster
        mocks['get_or_create_pg_user'].return_value = {'name': 'admin'}
        with mock.patch('builtins.print'):
            do_init.init_do_infra()
        return mocks

    def test_existing_droplet_is_not_created(self) -> None:
        mocks = self.run_init(Choice(DROPLETS[0], 'web'))
        mocks['get_or_create_droplet'].assert_not_called()
        mocks['save_env_option'].assert_any_call('DO_DROPLET_NAME', 'web')
        mocks['ensure_a_record'].assert_called_once_with('demo.example.com', '1.1.1.1')
        mocks['add_pg_firewall_rule'].assert_called_once_with('c1', 1)

    def test_new_droplet_is_created(self) -> None:
        mocks = self.run_init(Choice(None, 'demo-app'))
        mocks['get_or_create_droplet'].assert_called_once_with('demo-app', 'p1')
        mocks['add_pg_firewall_rule'].assert_called_once_with('c1', 3)

    def test_new_droplet_without_ssh_key_stops_before_changes(self) -> None:
        mocks = self.run_init(Choice(None, 'demo-app'), fingerprint='')
        for name in ('save_env_option', 'get_or_create_project', 'get_or_create_droplet',
                     'ensure_a_record', 'get_or_create_pg_cluster'):
            mocks[name].assert_not_called()

    def test_database_settings_are_written(self) -> None:
        mocks = self.run_init(Choice(DROPLETS[0], 'web'))
        mocks['save_env_option'].assert_any_call('DATABASE_NAME', 'demo')
        mocks['save_env_option'].assert_any_call('DATABASE_PORT', '25060')
        mocks['get_or_create_pg_database'].assert_called_once_with('c1', 'demo')


if __name__ == '__main__':
    unittest.main()
