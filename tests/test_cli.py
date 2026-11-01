"""`dc` command line and env files. Run from the project root: python3.15 -m unittest discover deploy/tests"""
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts.constants reads the environment on import; handlers are resolved in the tests.
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts import cli, env_files  # noqa: E402
from scripts.env_files import EnvFileError, parse_env_file  # noqa: E402


def write(text: str) -> str:
    handle = tempfile.NamedTemporaryFile('w', suffix='.env', delete=False, encoding='utf-8')
    handle.write(text)
    handle.close()
    return handle.name


class EnvFileTest(unittest.TestCase):
    def test_syntax(self) -> None:
        path = write(
            '# comment\n'
            '\n'
            'PLAIN=value\n'
            'export EXPORTED=1\n'
            "SINGLE='a $PLAIN #b'\n"
            'DOUBLE="x ${PLAIN} \\"q\\""\n'
            'UNQUOTED=$PLAIN/path # trailing comment\n'
            'EMPTY=\n'
            'TILDE=~/certs\n'
            'FROM_SHELL=$HOME_DIR\n'
        )
        values = parse_env_file(path, {'HOME_DIR': '/home/me'})
        self.assertEqual(values['PLAIN'], 'value')
        self.assertEqual(values['EXPORTED'], '1')
        self.assertEqual(values['SINGLE'], 'a $PLAIN #b')
        self.assertEqual(values['DOUBLE'], 'x value "q"')
        self.assertEqual(values['UNQUOTED'], 'value/path')
        self.assertEqual(values['EMPTY'], '')
        self.assertEqual(values['TILDE'], '~/certs')
        self.assertEqual(values['FROM_SHELL'], '/home/me')

    def test_errors(self) -> None:
        with self.assertRaisesRegex(EnvFileError, ':1: expected KEY=value'):
            parse_env_file(write('not a variable\n'))
        with self.assertRaisesRegex(EnvFileError, 'unterminated'):
            parse_env_file(write('A="open\n'))

    def test_layers_and_environments(self) -> None:
        base = write('PROJECT_NAME=demo\nURL=https://$PROJECT_NAME\n')
        dev = write('URL=http://localhost\nDEBUG=1\n')
        prod = write('SECRET=s\n')
        with mock.patch.multiple(env_files, BASE_ENV_FILE=base, DEV_ENV_FILE=dev, PROD_ENV_FILE=prod,
                                 LOCAL_ENV_FILE='/nonexistent/.env'):
            self.assertEqual(env_files.read_environment('dev', {}), {
                'PROJECT_NAME': 'demo', 'URL': 'http://localhost', 'DEBUG': '1',
            })
            # Production never sees the dev settings.
            self.assertEqual(env_files.read_environment('prod', {}), {
                'PROJECT_NAME': 'demo', 'URL': 'https://demo', 'SECRET': 's',
            })
        with mock.patch.multiple(env_files, BASE_ENV_FILE=base, PROD_ENV_FILE='/nonexistent/env.prod',
                                 LOCAL_ENV_FILE='/nonexistent/.env'):
            with self.assertRaisesRegex(EnvFileError, 'env.prod is required for prod commands'):
                env_files.read_environment('prod', {})


class RegistryTest(unittest.TestCase):
    def test_handlers_resolve_and_names_are_unique(self) -> None:
        cli.index_commands(cli.COMMANDS)
        for item in (*cli.COMMANDS, cli.COMPOSE_PASSTHROUGH):
            self.assertTrue(callable(cli._resolve(item.handler)), item.handler)

    def test_commands_without_environment_import_without_it(self) -> None:
        deploy_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {key: value for key, value in os.environ.items() if key not in ('PROJECT_NAME', 'PROJECT_DOMAIN')}
        for item in cli.COMMANDS:
            if item.env is None:
                code = f'from scripts import cli; cli._resolve({item.handler!r})'
                result = subprocess.run([sys.executable, '-c', code], cwd=deploy_dir, env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f'{item.name}: {result.stderr}')

    def test_production_commands_do_not_touch_dev_settings(self) -> None:
        for item in cli.COMMANDS:
            if item.env == 'prod':
                self.assertFalse(item.compose, item.name)


class ExecuteTest(unittest.TestCase):
    def run_command(self, name: str, args: list[str]) -> tuple[mock.Mock, mock.Mock, mock.Mock]:
        handler = mock.Mock()
        load = mock.Mock()
        generate = mock.Mock()
        config = mock.Mock()
        with mock.patch.object(cli, 'load_environment', load), \
                mock.patch.object(cli, '_resolve', return_value=handler), \
                mock.patch('scripts.compose.generate.generate_dev_compose_files', generate), \
                mock.patch('scripts.deploy_config.get_deploy_config', return_value=config):
            self.assertEqual(cli.main([name, *args]), 0)
        return load, generate, handler

    def test_deployprod_loads_prod_and_does_not_generate_dev_compose(self) -> None:
        load, generate, handler = self.run_command('deployprod', [])
        load.assert_called_once_with('prod')
        generate.assert_not_called()
        handler.assert_called_once_with()

    def test_dev_command_generates_compose_and_gets_args(self) -> None:
        load, generate, handler = self.run_command('migrate', ['--fake'])
        load.assert_called_once_with('dev')
        generate.assert_called_once_with()
        handler.assert_called_once_with(['--fake'])

    def test_unknown_command_goes_to_docker_compose(self) -> None:
        _load, generate, handler = self.run_command('logs', ['-f', 'fastapi'])
        generate.assert_called_once_with()
        handler.assert_called_once_with(['logs', '-f', 'fastapi'])


if __name__ == '__main__':
    unittest.main()
