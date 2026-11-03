"""Env file editing. Run from the project root: python3.15 -m unittest discover deploy/tests"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# scripts.helpers reads the project environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts.helpers import save_env_option  # noqa: E402


class SaveEnvOptionTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.env_file = os.path.join(directory.name, 'env.prod')
        environ = mock.patch.dict(os.environ)
        environ.start()
        self.addCleanup(environ.stop)

    def write(self, content: str) -> None:
        with open(self.env_file, 'w', encoding='utf-8') as f:
            f.write(content)

    def read(self) -> str:
        with open(self.env_file, encoding='utf-8') as f:
            return f.read()

    def test_replaces_only_the_exact_option(self) -> None:
        self.write('NEXT_PUBLIC_VERSION=0.1\nPROJECT_VERSION=0.1\nexport S3_SIGNATURE_VERSION=s3v4\n')
        save_env_option('PROJECT_VERSION', '0.2', self.env_file)
        self.assertEqual(self.read(), 'NEXT_PUBLIC_VERSION=0.1\nPROJECT_VERSION=0.2\nexport S3_SIGNATURE_VERSION=s3v4\n')
        self.assertEqual(os.environ['PROJECT_VERSION'], '0.2')

    def test_replaces_exported_option(self) -> None:
        self.write('export DATABASE_HOST=old\n')
        save_env_option('DATABASE_HOST', 'new', self.env_file)
        self.assertEqual(self.read(), 'DATABASE_HOST=new\n')

    def test_appends_missing_option_to_the_file(self) -> None:
        self.write('A=1')
        save_env_option('PROJECT_VERSION', '0.7', self.env_file, create=True)
        self.assertEqual(self.read(), 'A=1\nPROJECT_VERSION=0.7\n')
        self.assertEqual(os.environ['PROJECT_VERSION'], '0.7')

    def test_missing_option_without_create_fails(self) -> None:
        self.write('A=1\n')
        with self.assertRaises(Exception):
            save_env_option('B', '2', self.env_file)
        self.assertEqual(self.read(), 'A=1\n')


if __name__ == '__main__':
    unittest.main()
