"""MCP over HTTP: Streamable HTTP (2025-03-26+) and the legacy SSE transport.

Same contract as :func:`aurora_cli.mcp_transport.call_stdio`: returns
``(message, None)`` with the JSON-RPC response, or ``(None, error)``. A call
whose answer was lost is reported, never replayed automatically.
"""
from __future__ import annotations

import hashlib
import json
import queue
import threading
import time
from urllib.parse import urljoin

import httpx

PROTOCOL = "2025-06-18"
SUPPORTED = {"2024-11-05", "2025-03-26", "2025-06-18"}
MAX_MESSAGE = 1_000_000
CLIENT = {"name": "jobia-cli", "version": "1.7.0"}


class _RPCError(Exception):
    def __init__(self, error):
        self.error = error if isinstance(error, dict) else {"message": str(error)}


def _sse_messages(lines):
    data, event = [], "message"
    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                yield event, "\n".join(data)
            data, event = [], "message"
        elif line.startswith(":"):
            continue
        else:
            field, _, value = line.partition(":")
            value = value[1:] if value.startswith(" ") else value
            if field == "data":
                data.append(value)
            elif field == "event":
                event = value or "message"
    if data:
        yield event, "\n".join(data)


class StreamableSession:
    """One logical MCP session against a Streamable HTTP endpoint."""

    def __init__(self, url: str, headers: dict | None = None):
        self.url = url
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.session_id = None
        self.protocol = PROTOCOL
        self.initialized = False
        self.next_id = 0
        self.lock = threading.Lock()

    def _headers(self):
        headers = {"Accept": "application/json, text/event-stream",
                   "Content-Type": "application/json", **self.headers}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self.initialized:
            headers["MCP-Protocol-Version"] = self.protocol
        return headers

    def _post(self, client, message, deadline):
        timeout = max(1.0, deadline - time.monotonic())
        body = json.dumps(message, ensure_ascii=False, allow_nan=False)
        if len(body) > MAX_MESSAGE:
            raise ValueError("MCP request too large")
        with client.stream("POST", self.url, content=body, headers=self._headers(),
                           timeout=httpx.Timeout(timeout, connect=min(timeout, 10.0))) as response:
            if response.status_code == 404 and self.session_id:
                self.session_id, self.initialized = None, False
                raise ConnectionResetError("MCP session expired")
            if response.status_code in (202, 204) or not ("method" in message and "id" in message):
                response.read()
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError("MCP notification refused", request=response.request,
                                                response=response)
                return None
            response.raise_for_status()
            sid = response.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            kind = response.headers.get("content-type", "").split(";")[0].strip().lower()
            if kind == "text/event-stream":
                for _, data in _sse_messages(response.iter_lines()):
                    if len(data) > MAX_MESSAGE:
                        raise ValueError("MCP server response too large")
                    found = self._match(client, json.loads(data), message["id"], deadline)
                    if found is not None:
                        return found
                    if time.monotonic() >= deadline:
                        break
                raise TimeoutError("MCP stream ended without a response; outcome unknown")
            raw = response.read()
            if len(raw) > MAX_MESSAGE:
                raise ValueError("MCP server response too large")
            found = self._match(client, json.loads(raw), message["id"], deadline)
            if found is None:
                raise ValueError("MCP response does not answer the request")
            return found

    def _match(self, client, payload, request_id, deadline):
        for msg in payload if isinstance(payload, list) else [payload]:
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise ValueError("Invalid MCP JSON-RPC envelope")
            if "method" in msg and "id" in msg:
                # Only ping is answered: no sampling/elicitation is advertised.
                answer = ({"result": {}} if msg["method"] == "ping" else
                          {"error": {"code": -32601, "message": "Unsupported client method"}})
                self._post(client, {"jsonrpc": "2.0", "id": msg["id"], **answer}, deadline)
                continue
            if msg.get("id") == request_id:
                if "error" in msg:
                    raise _RPCError(msg["error"])
                if not isinstance(msg.get("result"), dict):
                    raise ValueError("Invalid MCP result")
                return msg
        return None

    def request(self, client, method, params, deadline):
        if not self.initialized:
            self.next_id += 1
            hello = self._post(client, {"jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
                                        "params": {"protocolVersion": PROTOCOL, "capabilities": {},
                                                   "clientInfo": CLIENT}}, deadline)
            version = hello["result"].get("protocolVersion")
            if version not in SUPPORTED:
                raise ValueError("Unsupported MCP protocol version")
            self.protocol = version
            self.initialized = True
            self._post(client, {"jsonrpc": "2.0", "method": "notifications/initialized"}, deadline)
        self.next_id += 1
        return self._post(client, {"jsonrpc": "2.0", "id": self.next_id, "method": method,
                                   "params": params}, deadline)


class LegacySSESession:
    """2024-11-05 transport: a GET event stream plus an announced POST endpoint."""

    def __init__(self, url: str, headers: dict | None = None):
        self.url = url
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.messages = queue.Queue(maxsize=256)
        self.endpoint = None
        self.ready = threading.Event()
        self.closed = threading.Event()
        self.client = httpx.Client(timeout=httpx.Timeout(None, connect=10.0), follow_redirects=True)
        self.lock = threading.Lock()
        self.next_id = 0
        self.initialized = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            with self.client.stream("GET", self.url, headers={"Accept": "text/event-stream", **self.headers}) as response:
                response.raise_for_status()
                for event, data in _sse_messages(response.iter_lines()):
                    if self.closed.is_set():
                        return
                    if event == "endpoint":
                        self.endpoint = urljoin(self.url, data.strip())
                        self.ready.set()
                    elif len(data) <= MAX_MESSAGE:
                        try:
                            self.messages.put(json.loads(data), timeout=1)
                        except (ValueError, queue.Full):
                            continue
        except (httpx.HTTPError, OSError):
            pass
        finally:
            self.closed.set()
            self.ready.set()

    def _send(self, message, deadline):
        if not self.ready.wait(max(0.0, deadline - time.monotonic())) or not self.endpoint:
            raise TimeoutError("MCP SSE endpoint not announced")
        response = self.client.post(self.endpoint, json=message, headers=self.headers,
                                    timeout=httpx.Timeout(max(1.0, deadline - time.monotonic()), connect=10.0))
        response.raise_for_status()

    def _await(self, request_id, deadline):
        while time.monotonic() < deadline:
            try:
                msg = self.messages.get(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if self.closed.is_set():
                    raise ConnectionResetError("MCP SSE stream closed; outcome unknown")
                continue
            if isinstance(msg, dict) and "method" in msg and "id" in msg:
                answer = ({"result": {}} if msg["method"] == "ping" else
                          {"error": {"code": -32601, "message": "Unsupported client method"}})
                self._send({"jsonrpc": "2.0", "id": msg["id"], **answer}, deadline)
                continue
            if isinstance(msg, dict) and msg.get("id") == request_id:
                if "error" in msg:
                    raise _RPCError(msg["error"])
                if not isinstance(msg.get("result"), dict):
                    raise ValueError("Invalid MCP result")
                return msg
        raise TimeoutError("MCP timed out; outcome unknown")

    def request(self, client, method, params, deadline):
        if not self.initialized:
            self.next_id += 1
            self._send({"jsonrpc": "2.0", "id": self.next_id, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": CLIENT}},
                       deadline)
            hello = self._await(self.next_id, deadline)
            if hello["result"].get("protocolVersion") not in SUPPORTED:
                raise ValueError("Unsupported MCP protocol version")
            self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, deadline)
            self.initialized = True
        self.next_id += 1
        self._send({"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params}, deadline)
        return self._await(self.next_id, deadline)

    def close(self):
        self.closed.set()
        self.client.close()


_sessions: dict[str, object] = {}
_pool = threading.Lock()


def _key(server):
    material = json.dumps([server.get("url"), server.get("transport"), server.get("headers", {})], sort_keys=True)
    return hashlib.sha256(material.encode()).hexdigest()


def _session_for(server: dict, legacy: bool, *, fresh: bool = False):
    with _pool:
        key = _key(server)
        session = None if fresh else _sessions.get(key)
        if session is None or (isinstance(session, LegacySSESession) and session.closed.is_set()) \
                or isinstance(session, LegacySSESession) != legacy:
            session = (LegacySSESession if legacy else StreamableSession)(server["url"], server.get("headers"))
            _sessions[key] = session
        return session


def _run(session, http, method, params, deadline, timeout):
    if not session.lock.acquire(timeout=timeout):
        raise BlockingIOError("MCP session busy; request was not sent")
    try:
        for attempt in (1, 2):
            try:
                return session.request(http, method, params, deadline)
            except ConnectionResetError:
                # An expired Streamable session is re-initialised once; an action
                # whose answer was lost is never replayed.
                if attempt == 2 or method == "tools/call":
                    raise
    finally:
        session.lock.release()


def call_http(server: dict, method: str, params: dict, *, timeout: float = 12.0, client=None):
    url = server.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        return None, "MCP HTTP server needs an http(s) url"
    legacy = server.get("transport") == "sse"
    deadline = time.monotonic() + timeout
    http = client or httpx.Client(follow_redirects=True)
    try:
        session = _session_for(server, legacy)
        try:
            return _run(session, http, method, params, deadline, timeout), None
        except httpx.HTTPStatusError as exc:
            # Spec fallback: an older server refuses the initialize POST with
            # 4xx because it speaks the 2024-11-05 SSE transport instead.
            if legacy or session.initialized or exc.response.status_code not in (400, 404, 405):
                raise
            session = _session_for(server, True, fresh=True)
            return _run(session, http, method, params, deadline, timeout), None
    except BlockingIOError as exc:
        return None, str(exc)
    except _RPCError as exc:
        return None, f"MCP JSON-RPC error {exc.error.get('code')}: {exc.error.get('message', '')}".strip()
    except (httpx.HTTPError, OSError, ValueError, TimeoutError) as exc:
        with _pool:
            _sessions.pop(_key(server), None)
        return None, f"MCP HTTP request failed ({type(exc).__name__}: {exc}); outcome unknown for actions"
    finally:
        if client is None:
            http.close()


def close_http(server: dict) -> bool:
    with _pool:
        session = _sessions.pop(_key(server), None)
    if session is not None and hasattr(session, "close"):
        session.close()
    return session is not None
