"""The connection flow must present authority before saving credentials."""

import importlib
import socket
import sys
import unittest
from unittest import mock

import httpx
from click.testing import CliRunner


class ConnectAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        resolver, excepthook = socket.getaddrinfo, sys.excepthook
        with mock.patch("aurora_cli.core.paths.init_app_dirs"):
            cls.entry = importlib.import_module("aurora_cli.cli")
        socket.getaddrinfo = resolver
        sys.excepthook = excepthook

    def connect(self, status, args, input=None):
        response = httpx.Response(status, json={"ok": status == 200},
                                  request=httpx.Request("POST", "https://fixture/api/cli/register"))
        with mock.patch.object(self.entry.httpx, "post", return_value=response) as post, \
                mock.patch.object(self.entry.config, "get", return_value=""), \
                mock.patch.object(self.entry.config, "set_key") as save, \
                mock.patch.object(self.entry, "AuroraClient") as client, \
                mock.patch.object(self.entry, "run_interactive") as interactive:
            result = CliRunner().invoke(self.entry.main, ["connect", "--server", "https://fixture", *args], input=input)
        self.assertIsNone(result.exception, result.output)
        return result, post, save, client, interactive

    def test_authorized_key_is_sent_before_config_is_saved(self):
        result, post, save, client, interactive = self.connect(200, ["--api-key", "fixture-key"])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer fixture-key")
        self.assertEqual(post.call_args.kwargs["json"]["client_key"], "fixture-key")
        self.assertEqual(save.call_count, 2)
        interactive.assert_called_once_with(client.return_value)
        client.return_value.close.assert_called_once()
        self.assertNotIn("fixture-key", result.output)

    def test_rejected_key_is_not_saved_and_does_not_open_session(self):
        _, _, save, client, interactive = self.connect(401, ["--api-key", "revoked-key"])
        save.assert_not_called()
        interactive.assert_not_called()
        client.assert_not_called()

    def test_first_connection_prompts_without_echoing_key(self):
        result, post, _, _, _ = self.connect(200, [], input="hidden-key\n")
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer hidden-key")
        self.assertNotIn("hidden-key", result.output)

    def test_expired_tunnel_falls_back_to_discovery(self):
        import base64
        discovered_b64 = base64.b64encode(b"https://fresh-tunnel.trycloudflare.com").decode()
        github_resp = httpx.Response(200, json={"encoding": "base64", "content": discovered_b64},
                                     request=httpx.Request("GET", "https://api.github.com"))
        dead_request = httpx.Request("POST", "https://old-tunnel.trycloudflare.com/api/cli/register")
        fresh_resp = httpx.Response(200, json={"ok": True},
                                    request=httpx.Request("POST", "https://fresh-tunnel.trycloudflare.com/api/cli/register"))

        def mock_post(url, **kwargs):
            if "old-tunnel" in url:
                raise httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known", request=dead_request)
            return fresh_resp

        with mock.patch.object(self.entry.httpx, "get", return_value=github_resp), \
                mock.patch.object(self.entry.httpx, "post", side_effect=mock_post), \
                mock.patch.object(self.entry.config, "resolve_server_url", return_value="https://old-tunnel.trycloudflare.com"), \
                mock.patch.object(self.entry.config, "get", return_value="saved-key"), \
                mock.patch.object(self.entry.config, "set_key") as save, \
                mock.patch.object(self.entry, "AuroraClient") as client, \
                mock.patch.object(self.entry, "run_interactive") as interactive:
            result = CliRunner().invoke(self.entry.main, ["connect"])
        self.assertIsNone(result.exception, result.output)
        save.assert_any_call("server_url", "https://fresh-tunnel.trycloudflare.com")
        interactive.assert_called_once()


if __name__ == "__main__":
    unittest.main()
