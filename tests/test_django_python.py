"""deploy.json django.python. Run from the project root: python3.15 -m unittest discover deploy/tests"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts.constants reads the environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts.compose.services import dev_django_service  # noqa: E402
from scripts.deploy_config import (  # noqa: E402
    DEFAULT_DJANGO_PYTHON, DeployConfig, DeployConfigError, DjangoConfig, _parse_django,
)

DOCKER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'docker')


class DjangoPythonTest(unittest.TestCase):
    def test_default(self) -> None:
        self.assertEqual(_parse_django({'django': {}}).python_image, f'python:{DEFAULT_DJANGO_PYTHON}-slim')

    def test_pinned_version(self) -> None:
        self.assertEqual(_parse_django({'django': {'python': '3.13.12'}}).python_image, 'python:3.13.12-slim')

    def test_invalid_versions(self) -> None:
        for value in ('3.13', '3', 'latest', '3.13.12-slim', 313, '2.7.18'):
            with self.subTest(value=value), self.assertRaises(DeployConfigError):
                _parse_django({'django': {'python': value}})

    def test_dev_build_arg(self) -> None:
        config = DeployConfig(django=DjangoConfig(enabled=True, python='3.13.12'), default_backend='django')
        self.assertEqual(dev_django_service(config)['build']['args'], {'PYTHON_IMAGE': 'python:3.13.12-slim'})

    def test_dockerfile_default_matches(self) -> None:
        for name in ('Dockerfile.djangodev', 'Dockerfile.djangoprod'):
            with open(os.path.join(DOCKER_DIR, name), encoding='utf-8') as f:
                self.assertIn(f'ARG PYTHON_IMAGE=python:{DEFAULT_DJANGO_PYTHON}-slim', f.read())


if __name__ == '__main__':
    unittest.main()
