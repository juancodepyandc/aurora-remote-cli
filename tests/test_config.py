import os
import unittest
from unittest.mock import patch
from aurora_cli.config import load, save, get, set_key, resolve_server_url, is_configured, PERMISSION_LEVELS, DEFAULT_CONFIG

class TestAuroraConfig(unittest.TestCase):
    def test_default_config(self):
        self.assertEqual(DEFAULT_CONFIG["default_permissions"], "AUTONOMOUS")
        self.assertIn("server_url", DEFAULT_CONFIG)
        self.assertIn("api_key", DEFAULT_CONFIG)

    def test_permission_levels(self):
        self.assertIn("SAFE", PERMISSION_LEVELS)
        self.assertIn("STANDARD", PERMISSION_LEVELS)
        self.assertIn("AUTONOMOUS", PERMISSION_LEVELS)
        self.assertIn("FULL", PERMISSION_LEVELS)

    def test_resolve_server_url_explicit(self):
        url = resolve_server_url("http://explicit-host:3001/")
        self.assertEqual(url, "http://explicit-host:3001")

    def test_resolve_server_url_env(self):
        with patch.dict(os.environ, {"AURORA_SERVER_URL": "http://env-host:5000"}):
            url = resolve_server_url()
            self.assertEqual(url, "http://env-host:5000")

    def test_is_configured_logic(self):
        with patch("aurora_cli.config.load", return_value={"server_url": "http://localhost:3001", "api_key": "test_key"}):
            self.assertTrue(is_configured())
        with patch("aurora_cli.config.load", return_value={"server_url": "", "api_key": ""}):
            self.assertFalse(is_configured())

if __name__ == "__main__":
    unittest.main()
