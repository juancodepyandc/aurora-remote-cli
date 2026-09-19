import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class ClientConfigTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.environment = mock.patch.dict(os.environ, {
            'XDG_DATA_HOME': str(root / 'data'),
            'XDG_CONFIG_HOME': str(root / 'config'),
            'AURORA_SERVER_URL': '',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        with mock.patch('pathlib.Path.home', return_value=root):
            from aurora_cli import config
            from aurora_cli.client import AuroraClient
        self.config = config
        self.client_type = AuroraClient
        for name, value in [('CONFIG_DIR', root / 'config'),
                            ('CONFIG_FILE', root / 'config' / 'config.json'),
                            ('SESSIONS_DIR', root / 'sessions')]:
            patch = mock.patch.object(config, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        config.save({'server_url': 'https://remote.example', 'api_key': 'fixture-key'})

    def client(self, **kwargs):
        client = self.client_type(**kwargs)
        self.addCleanup(client.close)
        return client

    def test_saved_server_is_used_without_override(self):
        self.assertEqual(self.client().server_url, 'https://remote.example')

    def test_local_wrapper_environment_overrides_saved_remote(self):
        os.environ['AURORA_SERVER_URL'] = 'http://127.0.0.1:3001/'
        client = self.client()
        self.assertEqual(client.server_url, 'http://127.0.0.1:3001')
        self.assertEqual(str(client._client.base_url), 'http://127.0.0.1:3001')

    def test_explicit_server_overrides_environment(self):
        os.environ['AURORA_SERVER_URL'] = 'http://127.0.0.1:3001'
        self.assertEqual(self.client(server_url='https://explicit.example/').server_url, 'https://explicit.example')

    def test_environment_does_not_replace_saved_server(self):
        os.environ['AURORA_SERVER_URL'] = 'http://127.0.0.1:3001'
        self.config.set_key('theme', 'light')
        os.environ.pop('AURORA_SERVER_URL')
        self.assertEqual(self.config.get('server_url'), 'https://remote.example')

    def test_configured_accepts_local_url_with_existing_key(self):
        self.config.save({'server_url': '', 'api_key': 'fixture-key'})
        os.environ['AURORA_SERVER_URL'] = 'http://127.0.0.1:3001'
        self.assertTrue(self.config.is_configured())

    def test_configured_still_requires_key(self):
        self.config.save({'server_url': '', 'api_key': ''})
        os.environ['AURORA_SERVER_URL'] = 'http://127.0.0.1:3001'
        self.assertFalse(self.config.is_configured())


if __name__ == '__main__':
    unittest.main()
