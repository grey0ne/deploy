"""Proxy site configs. Run from the project root: python3.15 -m unittest discover deploy/tests"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# scripts.constants reads the environment on import
os.environ.setdefault('PROJECT_NAME', 'demo')
os.environ.setdefault('PROJECT_DOMAIN', 'demo.example.com')

from scripts.compose.services import PROXY_DIR  # noqa: E402
from scripts.deploy_config import (  # noqa: E402
    DeployConfig, DjangoConfig, FastapiConfig, NextjsConfig,
)
from scripts.proxy import configuration  # noqa: E402

DJANGO_NEXTJS = DeployConfig(
    django=DjangoConfig(enabled=True, paths=('admin', 'api')),
    nextjs=NextjsConfig(enabled=True, backend='django'),
    default_backend='django',
    centrifugo_enabled=True,
)
BOTH_BACKENDS = DeployConfig(
    django=DjangoConfig(enabled=True, paths=('admin',)),
    fastapi=FastapiConfig(enabled=True, app='main:app', paths=('api', 'ws')),
    default_backend='fastapi',
)


def _routes(site: dict) -> dict[str, tuple[str, str | None]]:
    return {path: (route['upstream'], route.get('timeout')) for route in site['routes'] for path in route['paths']}


class ProxyConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        patcher = mock.patch.object(configuration.project_env, 'extra_domains', ['extra.example.com'])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_nextjs_catches_all(self) -> None:
        site = configuration.prod_sites(DJANGO_NEXTJS)[0]
        self.assertEqual(site['domains'], ['demo.example.com'])
        self.assertEqual(site['tls'], {'acme': True})
        self.assertEqual(_routes(site), {
            '/admin': ('http://demo-django:8000', '120s'),
            '/api': ('http://demo-django:8000', '120s'),
            '/': ('http://demo-nextjs:3000', None),
        })

    def test_default_backend_catches_all(self) -> None:
        site = configuration.prod_sites(BOTH_BACKENDS)[0]
        self.assertEqual(_routes(site), {
            '/admin': ('http://demo-django:8000', '120s'),
            '/': ('http://demo-fastapi:8080', '300s'),
        })

    def test_prod_sites(self) -> None:
        sites = configuration.prod_sites(DJANGO_NEXTJS)
        self.assertEqual(configuration.site_domains(sites), [
            'demo.example.com', 'centrifugo.demo.example.com', 'extra.example.com',
        ])
        self.assertTrue(all(site['tls'] == {'acme': True} for site in sites))
        self.assertEqual(_routes(sites[2]), {'/': ('http://demo-django:8000', '120s')})

    def test_dev_sites(self) -> None:
        sites = configuration.dev_sites(DJANGO_NEXTJS)
        self.assertEqual(configuration.site_domains(sites), [
            'demo.example.com', 'media.demo.example.com', 'centrifugo.demo.example.com', 'extra.example.com',
        ])
        self.assertEqual(
            [site['tls']['cert'] for site in sites],
            ['/app/ssl/demo.crt', '/app/ssl/media_demo.crt', '/app/ssl/centrifugo_demo.crt',
             '/app/ssl/demo_extra.example.com.crt'],
        )
        self.assertEqual(_routes(sites[1]), {
            '/demo-media': ('http://demo-minio:9000', None),
            '/': ('http://demo-minio:9001', None),
        })

    def test_no_media_site_without_django(self) -> None:
        config = DeployConfig(fastapi=FastapiConfig(enabled=True, app='main:app'), default_backend='fastapi')
        domains = configuration.site_domains(configuration.dev_sites(config))
        self.assertNotIn('media.demo.example.com', domains)

    @unittest.skipUnless(shutil.which('go'), 'go is not installed')
    def test_rendered_config_passes_proxy_check(self) -> None:
        """The YAML of dump_yaml is read by the proxy (go run, no image needed)."""
        with tempfile.TemporaryDirectory() as tmp:
            for name, sites in (('prod.yaml', configuration.prod_sites(DJANGO_NEXTJS)),
                                ('both.yaml', configuration.prod_sites(BOTH_BACKENDS))):
                if name == 'both.yaml':
                    for site in sites:
                        site['domains'] = [f'both-{domain}' for domain in site['domains']]
                with open(os.path.join(tmp, name), 'w', encoding='utf-8') as f:
                    f.write(configuration.render_sites(sites))
            result = subprocess.run(
                ['go', 'run', '.', '-check', '-config', tmp], cwd=PROXY_DIR, capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('2 files, 5 sites', result.stdout)


if __name__ == '__main__':
    unittest.main()
