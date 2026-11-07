# Shared stdlib transport, maintained from AuroraIA/agi_core/mcp_transport.py.
"""Bounded stdio MCP sessions. Uncertain calls are never replayed automatically."""
from __future__ import annotations

import atexit
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

MAX_MESSAGE = 1_000_000
MAX_SESSIONS = 8
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18"}


class SessionBusy(TimeoutError):
    pass


class RPCError(Exception):
    def __init__(self, code):
        self.code = code


class StdioSession:
    def __init__(self, server, cwd):
        command, args, env = server.get("command"), server.get("args", []), server.get("env", {})
        if not isinstance(command, str) or not command.strip():
            raise ValueError("MCP server command is empty")
        if not isinstance(args, list) or any(not isinstance(v, str) for v in args):
            raise ValueError("MCP args must be strings")
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
            raise ValueError("MCP environment must contain strings")
        self.proc = subprocess.Popen([command, *args], cwd=cwd, env={**os.environ, **env},
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.messages = queue.Queue(maxsize=64)
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.next_id = 0
        self.initialized = False
        self.last_used = time.monotonic()
        self.writer = None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self):
        try:
            while not self.closed.is_set():
                raw = self.proc.stdout.readline(MAX_MESSAGE + 1)
                if not raw or len(raw) > MAX_MESSAGE:
                    break
                try:
                    self.messages.put(raw, timeout=0.2)
                except queue.Full:
                    break
        finally:
            self.closed.set()

    def _send(self, message, deadline):
        raw = (json.dumps(message, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(raw) > MAX_MESSAGE:
            raise ValueError("MCP request too large")
        errors = []
        def write():
            try:
                self.proc.stdin.write(raw)
                self.proc.stdin.flush()
            except (OSError, ValueError) as exc:
                errors.append(exc)
        # A server that stops reading stdin must not block the caller indefinitely.
        writer = threading.Thread(target=write, daemon=True)
        self.writer = writer
        writer.start()
        writer.join(max(0, deadline - time.monotonic()))
        if writer.is_alive():
            raise TimeoutError("MCP send timed out; action outcome unknown, inspect before retry")
        if errors:
            raise OSError("MCP stdin closed")

    def _request(self, method, params, deadline):
        self.next_id += 1
        request_id = self.next_id
        self._send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}, deadline)
        total = 0
        while time.monotonic() < deadline:
            try:
                raw = self.messages.get(timeout=min(.1, max(.001, deadline - time.monotonic())))
            except queue.Empty:
                if self.closed.is_set():
                    raise RuntimeError("MCP server closed; action outcome unknown, inspect before retry")
                continue
            total += len(raw)
            if total > MAX_MESSAGE:
                raise ValueError("MCP server response too large")
            try:
                msg = json.loads(raw)
            except (ValueError, UnicodeError):
                raise ValueError("Invalid MCP JSON response") from None
            if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
                raise ValueError("Invalid MCP JSON-RPC envelope")
            if "method" in msg and "id" in msg:
                # No sampling/elicitation capability is advertised. Ping is safe.
                answer = {"result": {}} if msg["method"] == "ping" else {"error": {"code": -32601, "message": "Unsupported client method"}}
                self._send({"jsonrpc": "2.0", "id": msg["id"], **answer}, deadline)
                continue
            if type(msg.get("id")) is int and msg["id"] == request_id:
                if "error" in msg:
                    error = msg["error"]
                    raise RPCError(error.get("code") if isinstance(error, dict) else None)
                if not isinstance(msg.get("result"), dict):
                    raise ValueError("Invalid MCP result")
                return msg
        raise TimeoutError("MCP timed out; action outcome unknown, inspect before retry")

    def request(self, method, params, timeout):
        deadline = time.monotonic() + timeout
        if not self.lock.acquire(timeout=timeout):
            raise SessionBusy("MCP session busy; request was not sent")
        try:
            if self.closed.is_set():
                raise RuntimeError("MCP session closed; explicitly reconnect before retry")
            if not self.initialized:
                hello = self._request("initialize", {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "aurora-agi", "version": "1.3.0"},
                }, deadline)
                if hello["result"].get("protocolVersion") not in SUPPORTED_VERSIONS:
                    raise ValueError("Unsupported MCP protocol version")
                self._send({"jsonrpc": "2.0", "method": "notifications/initialized"}, deadline)
                self.initialized = True
            result = self._request(method, params, deadline)
            self.last_used = time.monotonic()
            return result
        finally:
            self.lock.release()

    def close(self):
        self.closed.set()
        # EOF allows native browser servers to close Chromium and contexts cleanly.
        if self.writer is None or not self.writer.is_alive():
            if self.proc.stdin and not self.proc.stdin.closed:
                self.proc.stdin.close()
            try:
                self.proc.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                pass
        # Termination also unblocks a writer whose pipe is full.
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2)
        for stream in (self.proc.stdin, self.proc.stdout):
            if stream:
                stream.close()
        self.reader.join(timeout=.3)


_sessions = {}
_pool_lock = threading.RLock()


def _key(server, cwd):
    return hashlib.sha256(json.dumps([str(Path(cwd or os.getcwd()).resolve()), server], sort_keys=True).encode()).hexdigest()


def close_stdio(server, *, cwd=None):
    """Explicitly release one configured session; never interrupt an active request."""
    with _pool_lock:
        key = _key(server, cwd)
        session = _sessions.get(key)
        if session is None:
            return False
        if not session.lock.acquire(blocking=False):
            raise SessionBusy("MCP session busy; wait for its current request")
        try:
            session.close()
            del _sessions[key]
        finally:
            session.lock.release()
        return True


def close_mcp_sessions():
    with _pool_lock:
        for session in _sessions.values():
            session.close()
        _sessions.clear()


def call_stdio(server, method, params, *, timeout=12.0, cwd=None):
    session = None
    persistent = server.get("persistent") is True
    try:
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("MCP timeout must be positive and finite")
        cwd = str(Path(cwd or os.getcwd()).resolve())
        key = _key(server, cwd)
        if persistent:
            with _pool_lock:
                # Evict only idle sessions: active calls must keep ownership.
                for old_key, old in list(_sessions.items()):
                    if not old.closed.is_set() and time.monotonic() - old.last_used > 600 and old.lock.acquire(blocking=False):
                        try:
                            old.close()
                            del _sessions[old_key]
                        finally:
                            old.lock.release()
                session = _sessions.get(key)
                if session is None:
                    if len(_sessions) >= MAX_SESSIONS:
                        raise RuntimeError("MCP session limit reached")
                    session = StdioSession(server, cwd)
                    _sessions[key] = session
                session.last_used = time.monotonic()
        else:
            session = StdioSession(server, cwd)
        return session.request(method, params, timeout), None
    except SessionBusy:
        return None, "MCP session busy; request was not sent"
    except RPCError as error:
        # A complete RPC error is a known response, not a broken transport.
        return None, f"MCP JSON-RPC error code {error.code}; session preserved"
    except (OSError, ValueError, RuntimeError, TimeoutError):
        if session is not None:
            session.close()
        # Keep a failed persistent session as a tombstone, preventing silent recreation/replay.
        return None, "MCP request failed or timed out; inspect state and explicitly close sessions before retry"
    finally:
        if session is not None and not persistent:
            session.close()


atexit.register(close_mcp_sessions)
