"""The shared balancer is only upgraded, never downgraded by a project with an older deploy.

Run from the project root: python3.15 -m unittest discover deploy/tests
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# scripts.constants reads the project environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts import commands, dev_commands, proxy_version  # noqa: E402
from scripts.compose.services import DEV_PROXY_IMAGE, PROXY_VERSION, prod_proxy_image  # noqa: E402
from scripts.errors import EnvConfigError  # noqa: E402

REPO = 'ghcr.io/grey0ne/deploy-proxy'


def bumped(version: str, delta: int) -> str:
    """A version above (delta > 0) or below (delta < 0) the given one: 0.2.0 → 0.2.1 / 0.1.99."""
    parts = [int(p) for p in version.split('.')]
    if delta < 0 and parts[-1] == 0:
        # 0.2.0 - 1 → 0.1.99
        index = max(i for i, p in enumerate(parts) if p > 0)
        parts[index] -= 1
        for i in range(index + 1, len(parts)):
            parts[i] = 99
    else:
        parts[-1] += delta
    return '.'.join(str(p) for p in parts)


class VersionTest(unittest.TestCase):
    def test_image_version(self) -> None:
        self.assertEqual(proxy_version.image_version(f'{REPO}:0.10.2'), (0, 10, 2))
        self.assertIsNone(proxy_version.image_version(f'{REPO}:latest'))
        self.assertIsNone(proxy_version.image_version('registry:5000/ns/deploy-proxy'))
        self.assertIsNone(proxy_version.image_version('nginx'))

    def test_numeric_comparison(self) -> None:
        self.assertTrue(proxy_version.is_newer(f'{REPO}:0.10.0', f'{REPO}:0.9.0'))
        self.assertFalse(proxy_version.is_newer(f'{REPO}:0.9.0', f'{REPO}:0.10.0'))
        self.assertFalse(proxy_version.is_newer(f'{REPO}:0.2.0', f'{REPO}:0.2.0'))

    def test_needs_update(self) -> None:
        current = f'{REPO}:0.2.0'
        self.assertFalse(proxy_version.needs_update(current, current))
        self.assertFalse(proxy_version.needs_update(f'{REPO}:0.3.0', current))
        self.assertTrue(proxy_version.needs_update(f'{REPO}:0.1.0', current))
        self.assertTrue(proxy_version.needs_update(None, current))
        # Not comparable: replaced by the current image
        self.assertTrue(proxy_version.needs_update(f'{REPO}:latest', current))
        self.assertTrue(proxy_version.needs_update('other/proxy:9.0.0', current))

    def test_check_image(self) -> None:
        current = f'{REPO}:0.2.0'
        self.assertEqual(proxy_version.check_image(f'{REPO}:0.3.0', current), f'{REPO}:0.3.0')
        self.assertEqual(proxy_version.check_image(f'{REPO}:0.1.0', current), current)
        self.assertEqual(proxy_version.check_image(None, current), current)


def with_tag(image: str, version: str) -> str:
    return f'{proxy_version.image_repository(image)}:{version}'


class ProdBalancerTest(unittest.TestCase):
    def ensure(self, deployed: str | None) -> mock.MagicMock:
        with mock.patch.object(commands, 'deployed_proxy_image', return_value=deployed), \
                mock.patch.object(commands, 'start_balancer') as start, \
                mock.patch.object(commands, 'print_warning'), mock.patch.object(commands, 'print_status'):
            commands.ensure_balancer()
        return start

    def test_older_is_upgraded(self) -> None:
        self.ensure(with_tag(prod_proxy_image(), bumped(PROXY_VERSION, -1))).assert_called_once()

    def test_same_is_kept(self) -> None:
        self.ensure(prod_proxy_image()).assert_not_called()

    def test_newer_is_kept(self) -> None:
        self.ensure(with_tag(prod_proxy_image(), bumped(PROXY_VERSION, 1))).assert_not_called()

    def test_nginx_is_not_switched(self) -> None:
        self.ensure(None).assert_not_called()

    def test_deploybalancer_refuses_a_downgrade(self) -> None:
        newer = with_tag(prod_proxy_image(), bumped(PROXY_VERSION, 1))
        with mock.patch.object(commands, 'check_registry_config'), \
                mock.patch.object(commands, 'deployed_proxy_image', return_value=newer), \
                mock.patch.object(commands, 'build_proxy_image') as build:
            with self.assertRaises(EnvConfigError):
                commands.setup_balancer()
        build.assert_not_called()


class DevBalancerTest(unittest.TestCase):
    def up(self, running: str | None) -> list[list[str]]:
        calls: list[list[str]] = []
        with mock.patch.object(dev_commands, 'ensure_dev_balancer'), \
                mock.patch('scripts.dev_balancer.running_dev_proxy_image', return_value=running), \
                mock.patch.object(dev_commands, 'running_dev_proxy_image', return_value=running), \
                mock.patch.object(dev_commands, 'print_status'), \
                mock.patch.object(dev_commands, 'run', side_effect=calls.append):
            dev_commands.up([])
        return calls

    def balancer_started(self, calls: list[list[str]]) -> bool:
        return any('dev_balancer.yml' in ' '.join(call) for call in calls)

    def test_newer_dev_proxy_is_kept(self) -> None:
        calls = self.up(f'deploy-proxy:{bumped(PROXY_VERSION, 1)}')
        self.assertFalse(self.balancer_started(calls))
        self.assertEqual(len(calls), 1)  # the project itself

    def test_older_or_missing_dev_proxy_is_started(self) -> None:
        self.assertTrue(self.balancer_started(self.up(f'deploy-proxy:{bumped(PROXY_VERSION, -1)}')))
        self.assertTrue(self.balancer_started(self.up(None)))
        self.assertTrue(self.balancer_started(self.up(DEV_PROXY_IMAGE)))


if __name__ == '__main__':
    unittest.main()
