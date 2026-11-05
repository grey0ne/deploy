"""deploy.json max_body_size. Run from the project root: python3.15 -m unittest discover deploy/tests"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts.constants reads the environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts.deploy_config import (  # noqa: E402
    DEFAULT_MAX_BODY_SIZE, DeployConfig, DeployConfigError, DjangoConfig, _parse_max_body_size,
)
from scripts.nginx import configuration  # noqa: E402


class MaxBodySizeTest(unittest.TestCase):
    def test_default_keeps_the_previous_limit(self) -> None:
        self.assertEqual(_parse_max_body_size({}), DEFAULT_MAX_BODY_SIZE)
        self.assertEqual(DEFAULT_MAX_BODY_SIZE, '50000k')

    def test_valid_sizes(self) -> None:
        for value in ('100m', '50000k', '1g', '1048576'):
            self.assertEqual(_parse_max_body_size({'max_body_size': value}), value)

    def test_invalid_sizes(self) -> None:
        for value in ('', '0', '100 m', '100mb', 100, '-1m', '10m; return 200'):
            with self.subTest(value=value), self.assertRaises(DeployConfigError):
                _parse_max_body_size({'max_body_size': value})

    def test_rendered_into_the_server(self) -> None:
        config = DeployConfig(django=DjangoConfig(enabled=True), default_backend='django', max_body_size='100m')
        with mock.patch.object(configuration, 'get_deploy_config', return_value=config):
            rendered = configuration._prod_server('demo.example.com', configuration.app_locations(config))
        self.assertIn('client_max_body_size 100m;', rendered)
        self.assertNotIn('{MAX_BODY_SIZE}', rendered)


if __name__ == '__main__':
    unittest.main()
