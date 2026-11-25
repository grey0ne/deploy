"""`dc removeprod` server script, run by bash against a stub `docker`.

Run from the project root: python3.15 -m unittest discover deploy/tests
"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# scripts.constants reads the project environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts import cli  # noqa: E402
from scripts import remove_production  # noqa: E402
from scripts.remove_production import removal_script  # noqa: E402

PREFIX = 'ghcr.io/grey0ne/demo'

# Records every call; `stack ls`, `ps` and `images` answer like a server running the demo stack
# (its containers are gone once `stack rm` ran) next to the stack of another project.
STUB_DOCKER = f"""#!/bin/bash
echo "$*" >> "$DOCKER_LOG"
case "$1 $2" in
    "stack ls") printf 'demo\\nother\\n' ;;
    "ps -aq")
        case "$*" in
            *namespace=demo) [ -f "$DOCKER_LOG.removed" ] || echo c1 ;;
        esac
        ;;
    "stack rm") touch "$DOCKER_LOG.removed" ;;
    "images --no-trunc")
        echo "sha256:1 {PREFIX}-django"
        echo "sha256:1 {PREFIX}-django"
        echo "sha256:2 {PREFIX}-nextjs"
        echo "sha256:3 {PREFIX}-extra-django"
        echo "sha256:4 ghcr.io/grey0ne/other-nextjs"
        echo "sha256:5 postgres"
        ;;
esac
"""


class RemovalScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        bin_dir = os.path.join(self.root, 'bin')
        os.makedirs(bin_dir)
        docker = os.path.join(bin_dir, 'docker')
        with open(docker, 'w', encoding='utf-8') as f:
            f.write(STUB_DOCKER)
        os.chmod(docker, os.stat(docker).st_mode | stat.S_IEXEC)
        self.log = os.path.join(self.root, 'docker.log')
        self.env = {**os.environ, 'PATH': f'{bin_dir}:{os.environ["PATH"]}', 'DOCKER_LOG': self.log}

        self.sites = os.path.join(self.root, 'sites')
        self.app = os.path.join(self.root, 'app', 'demo')
        os.makedirs(self.sites)
        os.makedirs(self.app)
        for name in ('demo.yaml', '.demo.yaml.new', 'other.yaml'):
            self.touch(os.path.join(self.sites, name))
        for name in ('prod.yml', 'env.base', 'env'):
            self.touch(os.path.join(self.app, name))

    @staticmethod
    def touch(path: str) -> None:
        with open(path, 'w', encoding='utf-8') as f:
            f.write('x')

    def run_script(self) -> list[str]:
        script = removal_script('demo', self.app, PREFIX, sites_dir=self.sites)
        subprocess.run(['bash', '-s'], input=script, encoding='utf-8', env=self.env, check=True,
                       stdout=subprocess.DEVNULL)
        with open(self.log, encoding='utf-8') as f:
            return f.read().splitlines()

    def test_removes_the_runtime(self) -> None:
        calls = self.run_script()
        self.assertIn('stack rm demo', calls)
        self.assertEqual(sorted(os.listdir(self.sites)), ['other.yaml'])
        # The project directory is empty, so it is removed too
        self.assertFalse(os.path.exists(self.app))

    def test_removes_only_the_project_images(self) -> None:
        calls = self.run_script()
        rmi = [call for call in calls if call.startswith('rmi')]
        self.assertEqual(rmi, ['rmi -f sha256:1 sha256:2'])

    def test_keeps_backend_data(self) -> None:
        data = os.path.join(self.app, 'backend_data')
        os.makedirs(data)
        self.touch(os.path.join(data, 'private.key'))
        self.run_script()
        self.assertEqual(os.listdir(self.app), ['backend_data'])
        self.assertTrue(os.path.exists(os.path.join(data, 'private.key')))

    def test_missing_stack_and_files(self) -> None:
        """A second run (nothing left) succeeds without `stack rm`."""
        for name in os.listdir(self.app):
            os.remove(os.path.join(self.app, name))
        script = removal_script('gone', self.app, 'ghcr.io/grey0ne/gone', sites_dir=self.sites)
        subprocess.run(['bash', '-s'], input=script, encoding='utf-8', env=self.env, check=True,
                       stdout=subprocess.DEVNULL)
        with open(self.log, encoding='utf-8') as f:
            calls = f.read().splitlines()
        self.assertFalse([call for call in calls if call.startswith(('stack rm', 'rmi'))])


class ConfirmationTest(unittest.TestCase):
    def test_project_name_confirms(self) -> None:
        with mock.patch('builtins.input', return_value='demo'):
            self.assertTrue(remove_production.confirmed([]))

    def test_other_answer_cancels(self) -> None:
        with mock.patch('builtins.input', return_value='yes'):
            self.assertFalse(remove_production.confirmed([]))

    def test_yes_flag_skips_the_prompt(self) -> None:
        with mock.patch('builtins.input', side_effect=AssertionError('prompted')):
            self.assertTrue(remove_production.confirmed(['--yes']))

    def test_cancelled_removal_runs_nothing(self) -> None:
        with mock.patch('builtins.input', return_value='no'), \
                mock.patch.object(remove_production, 'run_remote_commands') as remote:
            with self.assertRaises(SystemExit):
                remove_production.remove_production([])
        remote.assert_not_called()


class CommandTest(unittest.TestCase):
    def test_registered_as_production_command(self) -> None:
        command = cli.index_commands(cli.COMMANDS)['removeprod']
        self.assertEqual(command.env, 'prod')
        self.assertEqual(command.group, 'Production')
        self.assertFalse(command.compose)


if __name__ == '__main__':
    unittest.main()
