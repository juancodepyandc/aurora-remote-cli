"""Fault injection for resumable missions; no server or models required."""

import threading
import unittest
from unittest import mock

import httpx

from aurora_cli.client import AuroraClient
from aurora_cli.core.jobia import JOBIACore


class InterruptedBody(httpx.SyncByteStream):
    def __iter__(self):
        yield b'id: 1\ndata: {"type":"token","content":"alpha"}\n\n'
        yield b'id: 2\ndata: {"type":"token","content":"incomplete'
        raise httpx.ReadError("injected disconnect")


class StreamRecoveryTests(unittest.TestCase):
    def client(self, handler):
        patch = mock.patch("aurora_cli.client.httpx.HTTPTransport",
                           return_value=httpx.MockTransport(handler))
        patch.start()
        self.addCleanup(patch.stop)
        with mock.patch("aurora_cli.config.load", return_value={}):
            client = AuroraClient(server_url="http://fixture", api_key="fixture-key")
        self.addCleanup(client.close)
        return client

    def test_disconnect_resumes_after_last_complete_event(self):
        requests = []

        def handle(request):
            requests.append(request)
            if len(requests) == 1:
                return httpx.Response(200, stream=InterruptedBody())
            return httpx.Response(200, content=(
                b'id: 2\ndata:{"type":"token",\ndata: "content":"beta"}\n\n'
                b'id: 3\ndata: {"type":"mission_complete","result":"alphabeta"}\n\n'))

        client = self.client(handle)
        with mock.patch("time.sleep"):
            events = list(client.mission_stream("fixture"))
        tokens = [e["content"] for e in events if e["type"] == "token"]
        self.assertEqual(tokens, ["alpha", "beta"])
        self.assertEqual(events[-1]["type"], "mission_complete")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[1].headers["Last-Event-ID"], "1")
        self.assertTrue(all(r.method == "GET" for r in requests))

    def test_early_eof_retries_are_bounded(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, content=b": heartbeat\n\n")

        with mock.patch("time.sleep"):
            events = list(self.client(handle).mission_stream("fixture"))
        self.assertEqual(len(requests), 4)
        self.assertEqual(events[-1]["type"], "error")

    def test_post_stream_is_never_replayed(self):
        requests = []

        def handle(request):
            requests.append(request)
            return httpx.Response(200, stream=InterruptedBody())

        events = list(self.client(handle).chat_stream([{"role": "user", "content": "fixture"}]))
        self.assertEqual(len(requests), 1)
        self.assertEqual(events[-1]["type"], "error")

    def test_invalid_json_and_event_gaps_are_explicit_errors(self):
        for body in (b'id: 1\ndata: invalid\n\n',
                     b'id: 2\ndata: {"type":"token","content":"lost predecessor"}\n\n'):
            with self.subTest(body=body):
                client = self.client(lambda request: httpx.Response(200, content=body))
                events = list(client.mission_stream("fixture"))
                self.assertEqual(events[-1]["type"], "error")
                self.assertFalse(any(e["type"] == "token" for e in events))

    def test_auth_failure_is_not_retried(self):
        handler = mock.Mock(return_value=httpx.Response(401, json={"error": "invalid key"}))
        events = list(self.client(handler).mission_stream("fixture"))
        self.assertEqual(events[-1]["type"], "error")
        handler.assert_called_once()


class JobiaOutcomeTests(unittest.TestCase):
    def run_job(self, events=(), start=None):
        done = threading.Event()
        updates = []
        client = mock.Mock()
        client.mission_start.return_value = start or {"ok": True, "mission_id": "fixture"}
        client.mission_stream.return_value = iter(events)

        def callback(task_id, result):
            updates.append(result)
            if not result.get("stream") and not result.get("reconnecting"):
                done.set()

        JOBIACore().process_request("fixture", callback, client, session_id="session")
        self.assertTrue(done.wait(2), "worker did not finish")
        client.mission_start.assert_called_once_with("fixture", session_id="session")
        return updates

    def test_tokens_reach_ui_before_successful_completion(self):
        updates = self.run_job([{"type": "token", "content": "partial"},
                                {"type": "mission_complete", "result": "validated result"}])
        self.assertTrue(updates[0].get("stream"))
        self.assertEqual(updates[-1]["status"], "success")
        self.assertEqual(updates[-1]["data"], "validated result")

    def test_server_error_keeps_partial_text_and_is_not_success(self):
        updates = self.run_job([{"type": "token", "content": "partial"},
                                {"type": "error", "error": "GPU unavailable"}])
        self.assertEqual(updates[-1]["status"], "error")
        self.assertEqual(updates[-1]["partial_text"], "partial")
        self.assertIn("GPU unavailable", updates[-1]["data"])

    def test_missing_terminal_event_is_not_success(self):
        self.assertEqual(self.run_job()[0]["status"], "error")

    def test_start_failure_is_not_success(self):
        updates = self.run_job(start={"ok": False, "error": "daemon unavailable"})
        self.assertEqual(updates[-1]["status"], "error")
        self.assertIn("daemon unavailable", updates[-1]["data"])

    def test_stopped_mission_is_not_success(self):
        updates = self.run_job([{"type": "mission_complete", "stopped": True}])
        self.assertEqual(updates[-1]["status"], "stopped")


if __name__ == "__main__":
    unittest.main()
