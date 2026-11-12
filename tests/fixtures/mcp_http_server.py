"""Loopback MCP HTTP fixture: Streamable HTTP (JSON or SSE answers) and legacy SSE.

Started in a thread by tests; it answers only on 127.0.0.1 and executes nothing.
"""
from __future__ import annotations

import json
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _answer(message):
    method, params = message.get("method"), message.get("params", {})
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "fixture", "version": "1"}}
    if method == "tools/list":
        return {"tools": [{"name": "echo", "description": "Renvoie le texte reçu",
                           "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}}]}
    if method == "tools/call":
        text = params.get("arguments", {}).get("text", "")
        return {"content": [{"type": "text", "text": f"echo:{text}"}]}
    return None


class Fixture:
    def __init__(self, *, mode="json"):
        self.mode = mode  # json | sse | legacy
        self.posts = []
        self.sessions = set()
        self.legacy_queue = queue.Queue()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _body(self):
                return json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")

            def do_POST(self):
                message = self._body()
                fixture.posts.append((self.path, message, dict(self.headers)))
                if self.path.startswith("/messages"):
                    self.send_response(202)
                    self.end_headers()
                    if "id" in message and "method" in message:
                        result = _answer(message)
                        reply = {"jsonrpc": "2.0", "id": message["id"]}
                        reply.update({"result": result} if result is not None else
                                     {"error": {"code": -32601, "message": "Unsupported"}})
                        fixture.legacy_queue.put(reply)
                    return
                if fixture.mode == "legacy":
                    self.send_response(405)
                    self.end_headers()
                    return
                if "id" not in message or "method" not in message:
                    self.send_response(202)
                    self.end_headers()
                    return
                if message["method"] != "initialize" and self.headers.get("Mcp-Session-Id") not in fixture.sessions:
                    self.send_response(400)
                    self.end_headers()
                    return
                result = _answer(message)
                reply = {"jsonrpc": "2.0", "id": message["id"]}
                reply.update({"result": result} if result is not None else
                             {"error": {"code": -32601, "message": "Unsupported"}})
                body = json.dumps(reply).encode()
                self.send_response(200)
                if message["method"] == "initialize":
                    fixture.sessions.add("session-fixture")
                    self.send_header("Mcp-Session-Id", "session-fixture")
                if fixture.mode == "sse":
                    payload = b"event: message\ndata: " + body + b"\n\n"
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                else:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

            def do_GET(self):
                if self.path != "/sse" or fixture.mode != "legacy":
                    self.send_response(405)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"event: endpoint\ndata: /messages?session=1\n\n")
                self.wfile.flush()
                while not fixture.stopped.is_set():
                    try:
                        reply = fixture.legacy_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    try:
                        self.wfile.write(b"event: message\ndata: " + json.dumps(reply).encode() + b"\n\n")
                        self.wfile.flush()
                    except OSError:
                        return

        self.stopped = threading.Event()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.stopped.set()
        self.server.shutdown()
        self.server.server_close()
