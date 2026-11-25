"""`dc initinfra` DNS: the zone of a (sub)domain and only the A records of its exact name.

Run from the project root: python3.15 -m unittest discover deploy/tests
"""
import os
import sys
import unittest
from typing import Any
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# scripts.constants reads the project environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts import do_utils  # noqa: E402
from scripts.dns_records import Plan, find_zone, plan_a_records, record_name  # noqa: E402

API = 'https://api.digitalocean.com/v2/domains'


class ZoneTest(unittest.TestCase):
    def test_subdomain_is_in_the_parent_zone(self) -> None:
        self.assertEqual(find_zone('ap.grey.one', ['gry.one', 'grey.one']), 'grey.one')

    def test_most_specific_zone(self) -> None:
        self.assertEqual(find_zone('a.dev.grey.one', ['grey.one', 'dev.grey.one']), 'dev.grey.one')

    def test_zone_itself(self) -> None:
        self.assertEqual(find_zone('Drawr.App.', ['drawr.app']), 'drawr.app')

    def test_suffix_must_be_a_label(self) -> None:
        self.assertIsNone(find_zone('notgrey.one', ['grey.one']))
        self.assertIsNone(find_zone('ap.grey.one', []))

    def test_record_name(self) -> None:
        self.assertEqual(record_name('ap.grey.one', 'grey.one'), 'ap')
        self.assertEqual(record_name('a.b.grey.one', 'grey.one'), 'a.b')
        self.assertEqual(record_name('grey.one', 'grey.one'), '@')


def record(record_id: int, address: str) -> dict[str, Any]:
    return {'id': record_id, 'type': 'A', 'name': 'ap', 'data': address}


class PlanTest(unittest.TestCase):
    def test_create(self) -> None:
        self.assertEqual(plan_a_records([], '1.1.1.1'), Plan('create'))

    def test_keep(self) -> None:
        self.assertEqual(plan_a_records([record(1, '1.1.1.1')], '1.1.1.1').action, 'keep')

    def test_replace_keeps_the_matching_record(self) -> None:
        plan = plan_a_records([record(1, '9.9.9.9'), record(2, '1.1.1.1')], '1.1.1.1')
        self.assertEqual(plan.action, 'replace')
        self.assertEqual(plan.update, record(2, '1.1.1.1'))
        self.assertEqual(list(plan.delete), [record(1, '9.9.9.9')])


class EnsureARecordTest(unittest.TestCase):
    def run_ensure(self, records: list[dict[str, Any]], answer: str = '') -> tuple[bool, dict[str, mock.Mock]]:
        def get(url: str) -> dict[str, Any]:
            if url.startswith(f'{API}?'):
                return {'domains': [{'name': 'gry.one'}, {'name': 'grey.one'}], 'links': {}}
            self.assertEqual(url, f'{API}/grey.one/records?type=A&name=ap.grey.one&per_page=200')
            return {'domain_records': records}

        mocks = {
            'get': mock.patch.object(do_utils, 'do_get_request', side_effect=get).start(),
            'post': mock.patch.object(do_utils, 'do_post_request').start(),
            'put': mock.patch.object(do_utils, 'do_put_request').start(),
            'delete': mock.patch.object(do_utils, 'do_delete_request').start(),
        }
        mock.patch('builtins.print').start()
        self.addCleanup(mock.patch.stopall)
        ask = mock.Mock(return_value=answer)
        result = do_utils.ensure_a_record('ap.grey.one', '1.1.1.1', ask=ask)
        mocks['ask'] = ask
        return result, mocks

    def test_creates_the_subdomain_record_in_the_parent_zone(self) -> None:
        result, mocks = self.run_ensure([])
        self.assertTrue(result)
        mocks['post'].assert_called_once_with(
            f'{API}/grey.one/records', {'type': 'A', 'name': 'ap', 'data': '1.1.1.1', 'ttl': 1800},
        )
        mocks['ask'].assert_not_called()

    def test_up_to_date_record_is_not_touched(self) -> None:
        result, mocks = self.run_ensure([record(1, '1.1.1.1')])
        self.assertTrue(result)
        for name in ('post', 'put', 'delete', 'ask'):
            mocks[name].assert_not_called()

    def test_repointing_needs_confirmation(self) -> None:
        result, mocks = self.run_ensure([record(1, '9.9.9.9')], answer='n')
        self.assertFalse(result)
        for name in ('post', 'put', 'delete'):
            mocks[name].assert_not_called()

    def test_confirmed_repointing_updates_and_removes_the_others(self) -> None:
        result, mocks = self.run_ensure([record(1, '9.9.9.9'), record(2, '8.8.8.8')], answer='y')
        self.assertTrue(result)
        mocks['put'].assert_called_once_with(
            f'{API}/grey.one/records/1', {'type': 'A', 'name': 'ap', 'data': '1.1.1.1', 'ttl': 1800},
        )
        mocks['delete'].assert_called_once_with(f'{API}/grey.one/records/2')

    def test_zone_outside_the_account(self) -> None:
        with mock.patch.object(do_utils, 'do_get_request', return_value={'domains': [], 'links': {}}), \
                mock.patch.object(do_utils, 'do_post_request') as post, mock.patch('builtins.print'):
            self.assertFalse(do_utils.ensure_a_record('ap.grey.one', '1.1.1.1'))
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
