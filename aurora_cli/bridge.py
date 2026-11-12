"""HTTP and SSE client for the remote bridge."""
from __future__ import annotations

import json
import time
import base64
import hashlib
from uuid import uuid4
from pathlib import Path
import re
import os
from urllib.parse import urlsplit
from typing import Any, Generator

import httpx

from aurora_cli import brand, config
from aurora_cli.core import locations


def _sse_events(lines):
    """Decode complete SSE records; leave a truncated record for replay."""
    data = []
    event_id = None
    for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if data:
                event = json.loads("\n".join(data))
                if not isinstance(event, dict):
                    raise ValueError("SSE event must be a JSON object")
                yield event_id, event
            data = []
            event_id = None
        elif not line.startswith(":"):
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "data":
                data.append(value)
            elif field == "id":
                event_id = value


def _decode_json(response) -> dict:
    """Read a dict response, tolerating non-JSON proxy bodies."""
    try:
        payload = response.json()
    except (json.JSONDecodeError, ValueError, httpx.ResponseNotRead):
        return {"ok": False, "error": f"Invalid JSON response ({response.status_code})"}
    return payload if isinstance(payload, dict) else {"ok": False, "error": "Unexpected non-object response"}


def _request_failure(exc: httpx.RequestError, method: str, path: str, timeout) -> dict:
    """Keep a timeout's phase visible even when its exception has no message."""
    failure = {"ok": False, "error_kind": "connection", "error_type": type(exc).__name__}
    if isinstance(exc, httpx.TimeoutException):
        phase = next((name for cls, name in (
            (httpx.ConnectTimeout, "connect"), (httpx.ReadTimeout, "read"),
            (httpx.WriteTimeout, "write"), (httpx.PoolTimeout, "pool"),
        ) if isinstance(exc, cls)), "read")
        configured = timeout if isinstance(timeout, httpx.Timeout) else httpx.Timeout(timeout)
        seconds = getattr(configured, phase)
        label = {"connect": "connexion", "read": "lecture", "write": "envoi", "pool": "attente de connexion"}[phase]
        budget = f" ({seconds:g} s)" if seconds is not None else ""
        failure.update(error_kind="timeout", timeout_phase=phase, timeout_seconds=seconds,
                       error=f"{type(exc).__name__} : délai de {label} dépassé{budget} pour {method} {path}.")
        if method == "POST" and path == "/api/cli/mission/start":
            failure["acceptance_unknown"] = True
            failure["error"] += " L'acceptation de la mission est inconnue."
    else:
        detail = str(exc).strip() or "aucune réponse du transport"
        failure["error"] = f"{type(exc).__name__} pour {method} {path} : {detail}"
    return failure


_MISSION_PREFIX = "/api/cli/mission/"
#: Cloudflare answered for a bridge that never received the request.
_UNDELIVERED_STATUS = frozenset({502, 530})
#: The bridge or its tunnel is unavailable; delivery may be unknown (503/504).
_UNREACHABLE_STATUS = frozenset({502, 503, 504, 530})
_UNDELIVERED_ERRORS = frozenset({"ConnectError", "ConnectTimeout", "UnsupportedProtocol", "LocalProtocolError"})
_SAFE_POSTS = re.compile(r"^/api/cli/(auth|inputs|mission/[^/]+/stop|mcp/catalog)$")


def failure_kind(result: dict) -> str:
    """``undelivered``: the bridge never saw the request; ``unreachable``:
    the bridge or tunnel is unavailable and delivery is unknown; ``''`` else."""
    if not isinstance(result, dict) or result.get("ok"):
        return ""
    status = result.get("http_status")
    if result.get("error_type") in _UNDELIVERED_ERRORS or status in _UNDELIVERED_STATUS:
        return "undelivered"
    if result.get("error_kind") in {"connection", "timeout"} or status in _UNREACHABLE_STATUS:
        return "unreachable"
    return ""


class Bridge:
    """HTTP and SSE client for the remote bridge server.

    Every path is relative to the configured ``server_url``; the bridge owns
    all inference, so this class never loads a model itself.

    The Aurora PC gets a new quick-tunnel address each time it restarts. When
    the current address stops answering, any request (reads, writes, mission
    streams) re-reads the published address, checks that an Aurora bridge
    answers there and continues on it, then remembers it. Replays stay exact:
    reads are replayed freely, a mission start keeps its idempotency key (the
    bridge returns the same mission), and other writes are replayed only when
    the bridge provably never received them (no TCP/TLS connection, or a
    Cloudflare 502/530 for a missing origin). An accepted mission follows the
    server only after the new address confirms that it knows that mission.
    """

    #: Attempts and wall-clock window granted to a mission stream to survive
    #: a PC restart (the new tunnel is usually published within a minute).
    reconnect_attempts = 30
    reconnect_window = 300.0

    def __init__(self, server_url: str = "", api_key: str = "", timeout: float = 30.0):
        cfg = config.load()
        explicit = bool(server_url or os.environ.get(brand.env('server_url'))
                        or os.environ.get(brand.env_legacy('server_url')))
        self.server_url = config.resolve_server_url(server_url, cfg)
        self.api_key = api_key or config.get("api_key", "")
        self.timeout = timeout
        self._mission_hosts: dict[str, str] = {}
        # A saved (or missing) address follows the publication. An explicit
        # address does too when it is an ephemeral quick tunnel: such a
        # hostname is dead for good once its PC restarted.
        from . import tunnel
        self._discoverable = not explicit or tunnel.is_ephemeral(self.server_url)
        #: Optional ``callback(old_url, new_url)`` used by interfaces.
        self.on_relocate = None
        self._client = self._make_client(self.server_url)

    def _make_client(self, base_url: str, *, retries: int = 3, timeout=None) -> httpx.Client:
        # Robust transport: connection-level retries before any recovery.
        transport = httpx.HTTPTransport(retries=retries)
        budget = self.timeout if timeout is None else timeout
        return httpx.Client(
            transport=transport,
            base_url=base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(budget, connect=min(10.0, budget)),
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- Low-level ---

    @staticmethod
    def _mission_id(path: str) -> str:
        if path.startswith(_MISSION_PREFIX):
            mid = path[len(_MISSION_PREFIX):].split('/', 1)[0].split('?', 1)[0]
            if mid and mid != 'start':
                return mid
        return ''

    def _mission_path(self, path: str) -> str:
        mid = self._mission_id(path)
        if mid in self._mission_hosts:
            return self._mission_hosts[mid].rstrip('/') + path
        return path

    def _send(self, method: str, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        target = (self.server_url.rstrip('/') + path if path == '/api/cli/mission/start'
                  else self._mission_path(path))
        try:
            if method in {'POST', 'PUT', 'PATCH'}:
                kwargs = {'json': data or {}, **kwargs}
            r = self._client.request(method, target, **kwargs)
            r.raise_for_status()
            return _decode_json(r)
        except httpx.RequestError as e:
            return _request_failure(e, method, path, kwargs.get("timeout", self._client.timeout))
        except httpx.HTTPStatusError as e:
            payload = _decode_json(e.response)
            payload.update(ok=False, http_status=e.response.status_code)
            payload.setdefault("error", f"HTTP error {e.response.status_code}")
            return payload

    def _replay_safe(self, method: str, path: str, data: dict | None) -> bool:
        if method in {'GET', 'HEAD'}:
            return True
        if method == 'POST' and path == '/api/cli/mission/start':
            return bool((data or {}).get('idempotency_key'))
        return method == 'POST' and bool(_SAFE_POSTS.match(path))

    def request(self, method: str, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        """One request, continued on the PC's new address when it moved."""
        method = method.upper()
        result = self._send(method, path, data, **kwargs)
        kind = failure_kind(result)
        if not kind or (kind == 'unreachable' and not self._replay_safe(method, path, data)):
            return result
        moved = self._follow(path)
        if not moved:
            return result
        retried = self._send(method, path, data, **kwargs)
        self._remember(retried)
        if retried.get('ok') or not failure_kind(retried):
            retried.setdefault('relocated_to', moved)
        return retried

    def get(self, path: str, **kwargs: Any) -> dict:
        return self.request('GET', path, **kwargs)

    def post(self, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        result = self.request('POST', path, data, **kwargs)
        mid = result.get('mission_id')
        if (path == '/api/cli/mission/start' and result.get('ok') and isinstance(mid, str)
                and mid and '/' not in mid):
            self._mission_hosts[mid] = self.server_url
        return result

    def delete(self, path: str, **kwargs: Any) -> dict:
        return self.request('DELETE', path, **kwargs)

    # --- Following the PC to its new address ---

    def _probe(self, url: str) -> bool:
        """Is an Aurora bridge answering at ``url``? (public health route)"""
        try:
            with httpx.Client(transport=httpx.HTTPTransport(retries=1), follow_redirects=True,
                              timeout=httpx.Timeout(8.0, connect=6.0)) as client:
                for health_path in ('/api/health', '/api/ping'):
                    response = client.get(url.rstrip('/') + health_path)
                    if response.status_code in _UNREACHABLE_STATUS:
                        return False
                    try:
                        payload = response.json()
                    except ValueError:
                        continue
                    if (response.status_code == 200 and isinstance(payload, dict)
                            and payload.get('service') == 'aurora-bridge'):
                        return True
        except (httpx.HTTPError, OSError):
            return False
        return False

    def alive(self) -> dict:
        """Cheap public reachability of the current address, with its latency."""
        started = time.perf_counter()
        ok = bool(self.server_url) and self._probe(self.server_url)
        return {'ok': ok, 'latency_ms': (time.perf_counter() - started) * 1000}

    def relocate(self) -> str:
        """Re-read the published address; switch to it when a bridge answers.

        Returns the new address, or ``''`` when the PC did not move (or cannot
        be reached yet). Explicit non-tunnel addresses never move.
        """
        if not self._discoverable:
            return ''
        from .connect import discover_tunnel
        try:
            fresh = discover_tunnel().rstrip('/')
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            return ''
        if not fresh or fresh == self.server_url.rstrip('/') or not self._probe(fresh):
            return ''
        old, self.server_url = self.server_url, fresh
        self._client.base_url = fresh
        callback = self.on_relocate
        if callback is not None:
            try:
                callback(old, fresh)
            except Exception:
                pass
        return fresh

    def _follow(self, path: str) -> str:
        """Move the request target; accepted missions need proof on arrival."""
        mid = self._mission_id(path)
        pinned = self._mission_hosts.get(mid, '').rstrip('/') if mid else ''
        if pinned and pinned != self.server_url.rstrip('/'):
            moved = self.server_url  # Another request already followed the PC.
        else:
            moved = self.relocate()
        if not moved:
            return ''
        if pinned:
            if not self._known_mission(moved, mid):
                return ''
            self._mission_hosts[mid] = moved
        return moved

    def _known_mission(self, base: str, mid: str) -> bool:
        try:
            response = self._client.get(base.rstrip('/') + f'{_MISSION_PREFIX}{mid}/status',
                                        timeout=httpx.Timeout(15.0, connect=8.0))
            payload = response.json() if response.status_code == 200 else {}
        except (httpx.HTTPError, ValueError):
            return False
        return (isinstance(payload, dict) and payload.get('ok') is True
                and payload.get('id', mid) == mid)

    def _remember(self, result: dict) -> None:
        """Persist a followed address once the bridge accepted our identity."""
        if failure_kind(result) or result.get('http_status') in {401, 403}:
            return
        try:
            if config.resolve_server_url() != self.server_url:
                config.set_key('server_url', self.server_url)
        except OSError:
            pass

    def stream_sse(self, path: str, data: dict | None = None, method: str = "POST",
                   *, resume: bool = False, last_event_id: int = 0) -> Generator[dict, None, None]:
        """Resume mission GET streams from complete events, never replay POSTs."""
        if resume and method != "GET":
            raise ValueError("Only mission GET streams support replay")
        if type(last_event_id) is not int or last_event_id < 0:
            raise ValueError("Mission cursor must be a non-negative integer")
        retries = 0
        deadline = None
        stream_timeout = httpx.Timeout(600.0, connect=10.0)
        while True:
            headers = {"Last-Event-ID": str(last_event_id)} if resume else {}
            kwargs = {"json": data or {}} if method == "POST" else {}
            try:
                with self._client.stream(
                    method, self._mission_path(path), headers=headers,
                    timeout=stream_timeout,
                    **kwargs,
                ) as response:
                    if response.is_error:
                        response.read()
                    response.raise_for_status()
                    for event_id, event in _sse_events(response.iter_lines()):
                        if resume and event.get("type") != "heartbeat":
                            if event_id is None or not event_id.isascii() or not event_id.isdecimal():
                                raise ValueError("Mission stream has no valid event ID; upgrade the bridge")
                            cursor = int(event_id)
                            if cursor <= last_event_id:
                                continue
                            if cursor != last_event_id + 1:
                                raise ValueError("Missing mission events; refusing an incomplete result")
                            last_event_id = cursor
                            retries = 0
                            deadline = None
                        yield event
                        if resume and event.get("type") in ("mission_complete", "error", "mission_interrupted"):
                            return
                if not resume:
                    return
                error = "Mission stream ended before its terminal event"
                error_kind = "interrupted_stream"
            except httpx.RequestError as exc:
                failure = _request_failure(exc, method, path, stream_timeout)
                error, error_kind = failure["error"], failure["error_kind"]
            except httpx.HTTPStatusError as exc:
                payload = _decode_json(exc.response)
                error = payload.get("error") or f"HTTP error {exc.response.status_code}"
                error_kind = payload.get("error_kind") or "http"
                if exc.response.status_code not in (500, 502, 503, 504, 530):
                    yield {"type": "error", "error": error, "error_kind": error_kind,
                           "last_event_id": last_event_id}
                    return
            except ValueError as exc:
                yield {"type": "error", "error": f"Invalid SSE stream: {exc}",
                       "error_kind": "invalid_stream", "last_event_id": last_event_id}
                return
            if deadline is None:
                deadline = time.monotonic() + self.reconnect_window
            if not resume or retries >= self.reconnect_attempts or time.monotonic() >= deadline:
                yield {"type": "error", "error": error, "error_kind": error_kind,
                       "last_event_id": last_event_id}
                return
            retries += 1
            moved = self._follow(path) if error_kind != "interrupted_stream" else ''
            yield {"type": "reconnecting", "attempt": retries,
                   "last_event_id": last_event_id, "message": error, "error_kind": error_kind,
                   **({"relocated_to": moved} if moved else {})}
            if not moved:
                time.sleep(min(2 ** (retries - 1), 10))

    # --- Authentication ---

    def auth(self) -> dict:
        return self.post("/api/cli/auth")

    def ping(self) -> bool:
        try:
            r = self.get("/api/cli/status")
            return r.get("ok", False)
        except Exception:
            return False

    # --- Status & Info ---

    def status(self) -> dict:
        return self.get("/api/cli/status")

    def doctor(self) -> dict:
        return self.get('/api/cli/doctor')

    def version(self) -> dict:
        return self.get("/api/cli/version")

    def tools(self) -> dict:
        return self.get("/api/cli/tools")

    def models(self) -> dict:
        return self.get("/api/cli/models")

    # --- Chat ---

    def chat_stream(self, messages: list[dict], model: str = "",
                    session_id: str = "", workspace: str = "") -> Generator[dict, None, None]:
        data: dict[str, Any] = {"messages": messages}
        if model:
            data["model"] = model
        if session_id:
            data["session_id"] = session_id
        if workspace:
            data["workspace"] = workspace
        yield from self.stream_sse("/api/cli/chat", data)

    # --- Sessions ---

    def session_create(self, workspace: str = "", permissions: str = "AUTONOMOUS") -> dict:
        return self.post("/api/cli/session/create", {"workspace": workspace, "permissions": permissions})

    def session_list(self) -> dict:
        return self.get("/api/cli/session/list")

    def session_resume(self, session_id: str) -> dict:
        return self.post(f"/api/cli/session/{session_id}/resume")

    def session_delete(self, session_id: str) -> dict:
        return self.delete(f"/api/cli/session/{session_id}")

    # --- Permissions ---

    def permissions_get(self) -> dict:
        return self.get("/api/cli/permissions")

    def permissions_set(self, level: str, session_id: str = "") -> dict:
        return self.post("/api/cli/permissions", {"level": level, "session_id": session_id})

    # --- Agents ---

    def agents_official(self) -> dict:
        return self.get("/api/cli/agents/official")

    def agent_disable(self, name: str) -> dict:
        return self.post(f"/api/cli/agents/official/{name}/disable")

    def agent_enable(self, name: str) -> dict:
        return self.post(f"/api/cli/agents/official/{name}/enable")

    def agents_dynamic(self) -> dict:
        return self.get("/api/cli/agents/dynamic/list")

    def agent_dynamic_get(self, agent_id: str) -> dict:
        return self.get(f"/api/cli/agents/dynamic/{agent_id}")

    def agent_dynamic_create(self, **kwargs: Any) -> dict:
        return self.post("/api/cli/agents/dynamic/create", kwargs)

    def agent_dynamic_modify(self, agent_id: str, **kwargs: Any) -> dict:
        return self.post(f"/api/cli/agents/dynamic/{agent_id}/modify", kwargs)

    def agent_dynamic_delete(self, agent_id: str) -> dict:
        return self.delete(f"/api/cli/agents/dynamic/{agent_id}")

    def agent_dynamic_save(self, agent_id: str) -> dict:
        return self.post(f"/api/cli/agents/dynamic/{agent_id}/save")

    # --- MCP ---

    def mcp_list(self, workspace: str = "") -> dict:
        return self.get("/api/cli/mcp/list", params={"workspace": workspace} if workspace else {})

    def mcp_tools(self, workspace: str = "") -> dict:
        return self.get("/api/cli/mcp/tools", params={"workspace": workspace} if workspace else {})

    def mcp_call(self, server: str, tool: str, arguments: dict | None = None, workspace: str = "", timeout_seconds: float | None = None) -> dict:
        payload: dict[str, Any] = {"server": server, "tool": tool, "arguments": arguments or {}}
        if workspace:
            payload["workspace"] = workspace
        options = {}
        if timeout_seconds is not None:
            if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 180:
                raise ValueError("MCP timeout must be between 1 and 180 seconds")
            payload["timeout_seconds"] = timeout_seconds
            options["timeout"] = httpx.Timeout(timeout_seconds + 10, connect=10)
        return self.post("/api/cli/mcp/call", payload, **options)

    def mcp_catalog(self, server: str, kind: str = "tools", *, cursor: str = "", workspace: str = "") -> dict:
        params = {"server": server, "kind": kind}
        if cursor:
            params["cursor"] = cursor
        if workspace:
            params["workspace"] = workspace
        return self.get("/api/cli/mcp/catalog", params=params)

    def mcp_read_resource(self, server: str, uri: str, *, workspace: str = "") -> dict:
        return self.post("/api/cli/mcp/resource", {"server": server, "uri": uri, **({"workspace": workspace} if workspace else {})})

    def mcp_get_prompt(self, server: str, name: str, arguments: dict | None = None, *, workspace: str = "") -> dict:
        return self.post("/api/cli/mcp/prompt", {"server": server, "name": name, "arguments": arguments or {}, **({"workspace": workspace} if workspace else {})})

    def mcp_close(self, server: str, *, workspace: str = "") -> dict:
        return self.post("/api/cli/mcp/close", {"server": server, **({"workspace": workspace} if workspace else {})})

    # --- Skills ---

    def skills_list(self, workspace: str = "") -> dict:
        return self.get("/api/cli/skills/list", params={"workspace": workspace} if workspace else {})

    def skills_read(self, name: str, workspace: str = "") -> dict:
        payload: dict[str, Any] = {"name": name}
        if workspace:
            payload["workspace"] = workspace
        return self.post("/api/cli/skills/read", payload)

    def skills_create(self, name: str, description: str = "", level: str = "user",
                      triggers: list[str] | None = None, instructions: str = "", workspace: str = "") -> dict:
        payload: dict[str, Any] = {"name": name, "description": description, "level": level, "triggers": triggers or []}
        if instructions:
            payload["instructions"] = instructions
        if workspace:
            payload["workspace"] = workspace
        return self.post("/api/cli/skills/create", payload)

    def skills_discover(self, workspace: str = "") -> dict:
        return self.post("/api/cli/skills/discover", {"workspace": workspace})

    # --- Epistemic Intelligence (Tunnel + Automatic Local Fallback) ---

    def epistemic_evaluate(self, mode: str = "route", *, fallback_local: bool = True, **kwargs: Any) -> dict:
        """Evaluate an epistemic query over the tunnel, falling back to local epistemic engine if unreachable."""
        payload = {"mode": mode, **kwargs}
        res = self.post("/api/cli/epistemic/evaluate", payload)
        if res.get("ok"):
            res.setdefault("transport", "bridge")
            return res
        unavailable = (
            res.get("error_kind") in {"connection", "timeout"}
            or res.get("http_status") in {404, 500, 502, 503, 504, 530}
        )
        if not fallback_local or not unavailable:
            return res
        from . import epistemic
        m = (mode or "route").strip().lower()
        if m == "route":
            out = epistemic.classify_problem_regime(
                str(kwargs.get("prompt") or kwargs.get("hypothesis") or ""),
                code=str(kwargs.get("code") or ""),
                skills=kwargs.get("skills") if isinstance(kwargs.get("skills"), list) else None,
            )
        elif m == "math":
            out = epistemic.verify_math_hypothesis(
                str(kwargs.get("conjecture") or ""),
                domain=str(kwargs.get("domain") or "int"),
                hypothesis=str(kwargs.get("hypothesis") or ""),
            )
        elif m == "cyber":
            out = epistemic.verify_cyber_hypothesis(
                str(kwargs.get("code") or ""),
                hypothesis=str(kwargs.get("hypothesis") or ""),
            )
        elif m == "optimize":
            out = epistemic.verify_optimization_hypothesis(
                str(kwargs.get("baseline_code") or ""),
                str(kwargs.get("candidate_code") or ""),
                entry_function=str(kwargs.get("entry_function") or ""),
                test_inputs=kwargs.get("test_inputs") if isinstance(kwargs.get("test_inputs"), list) else None,
                hypothesis=str(kwargs.get("hypothesis") or ""),
            )
        elif m == "explore":
            out = epistemic.evaluate_competing_hypotheses(
                str(kwargs.get("problem") or kwargs.get("hypothesis") or ""),
                kwargs.get("hypotheses") if isinstance(kwargs.get("hypotheses"), list) else [],
            )
        else:
            return res
        out["transport"] = "local_fallback"
        return out

    # --- Connections ---

    def connections_list(self) -> dict:
        return self.get("/api/cli/connections/list")

    def connections_add(self, service: str, credentials: dict) -> dict:
        return self.post("/api/cli/connections/add", {"service": service, "credentials": credentials})

    def connections_remove(self, service: str) -> dict:
        return self.post("/api/cli/connections/remove", {"service": service})

    def connections_test(self, service: str) -> dict:
        return self.post("/api/cli/connections/test", {"service": service})

    # --- Missions ---

    def prepare_references(self, request: str, input_files=()) -> list[dict]:
        files = [Path(p).expanduser().resolve() for p in input_files]
        if re.search(r'\b3d\b',request,re.I):
            from .core.request_spec import parse_creation_request
            image = parse_creation_request(request).input_image
            if image is not None and image not in files:
                files.append(image)
        if len(files)>8:
            raise ValueError('Au maximum huit références par mission')
        references=[]
        for path in files:
            if path.name == '.env' or path.name.startswith('.env.') or any(
                    part in {'.ssh','.secrets','.git'} for part in path.parts):
                raise ValueError('Fichier privé/protégé refusé comme référence')
            if not path.is_file() or not 0 < path.stat().st_size <= 20*1024*1024:
                raise ValueError('Référence locale absente/vide ou supérieure à 20 Mo : '+str(path))
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            result = self.post('/api/cli/inputs', {'filename':path.name, 'size':len(data), 'sha256':digest,
                                                   'data':base64.b64encode(data).decode('ascii')})
            receipt = result.get('id','')
            if (not result.get('ok') or result.get('sha256') != digest or result.get('size') != len(data)
                    or not re.fullmatch(r'inp_'+digest+r'\.[a-z0-9]{1,8}',receipt)):
                raise RuntimeError('Transfert de référence non confirmé : '+str(result.get('error','empreinte invalide')))
            references.append({'id':receipt, 'label':str(path)})
        return references

    def mission_start(self, request: str, workspace: str = "", permissions: str = "AUTONOMOUS",
                      session_id: str = "", model: str = "", idempotency_key: str = "", history: list | None = None,
                      context_tokens: int | None = None, references: list | None = None) -> dict:
        # A key is always sent: the bridge then returns the same mission for
        # a replayed start, which makes reconnection after a restart exact.
        payload = {
            "request": request, "workspace": workspace, "permissions": permissions,
            "session_id": session_id, "model": model,
            "idempotency_key": idempotency_key or uuid4().hex,
        }
        if history is not None:
            payload['history'] = history
        if context_tokens is not None:
            if type(context_tokens) is not int or not 1024 <= context_tokens <= 131072:
                raise ValueError('context_tokens must be an integer from 1024 to 131072')
            payload['context_tokens'] = context_tokens
        references = self.prepare_references(request) if references is None else references
        if references:
            payload['references'] = references
        return self.post("/api/cli/mission/start", payload)

    def mission_stream(self, mission_id: str, last_event_id: int = 0) -> Generator[dict, None, None]:
        host = (urlsplit(self.server_url).hostname or '').lower()
        if host.endswith('.trycloudflare.com'):
            yield from self.mission_poll(mission_id, last_event_id)
            return
        yield from self.stream_sse(f"/api/cli/mission/{mission_id}/stream", method="GET",
                                  resume=True, last_event_id=last_event_id)

    def mission_poll(self, mission_id: str, last_event_id: int = 0) -> Generator[dict, None, None]:
        """Replay durable events through finite authenticated GET responses."""
        if type(last_event_id) is not int or last_event_id < 0:
            raise ValueError('Mission cursor must be a non-negative integer')
        retries = 0
        deadline = None
        while True:
            result = self.get(f'/api/cli/mission/{mission_id}/events',
                              headers={'Last-Event-ID':str(last_event_id)},
                              params={'wait':20}, timeout=30)
            if not result.get('ok'):
                status = result.get('http_status')
                retryable = status in (500,502,503,504,530) or result.get('error_kind') in ('timeout','connection')
                if deadline is None:
                    deadline = time.monotonic() + self.reconnect_window
                if not retryable or retries >= self.reconnect_attempts or time.monotonic() >= deadline:
                    yield {'type':'error','error':result.get('error','Mission polling failed; update the bridge'),
                           'error_kind':result.get('error_kind','http'),'last_event_id':last_event_id}
                    return
                retries += 1
                yield {'type':'reconnecting','attempt':retries,'last_event_id':last_event_id,
                       'message':result.get('error','Mission polling unavailable'),
                       'error_kind':result.get('error_kind','http')}
                time.sleep(min(2 ** (retries-1),10))
                continue
            try:
                rows = result['events']
                if not isinstance(rows,list):
                    raise ValueError('events must be an array')
                position = last_event_id
                accepted = []
                for row in rows:
                    cursor, event = row['id'], row['event']
                    if type(cursor) is not int or not isinstance(event,dict) or cursor != position+1:
                        raise ValueError('Missing or invalid mission events; refusing an incomplete result')
                    accepted.append(event)
                    position = cursor
                if result.get('cursor') != position or type(result.get('terminal')) is not bool:
                    raise ValueError('Invalid mission polling cursor or terminal state')
            except (KeyError,TypeError,ValueError) as exc:
                yield {'type':'error','error':str(exc),'error_kind':'invalid_stream','last_event_id':last_event_id}
                return
            retries = 0
            deadline = None
            for event in accepted:
                last_event_id += 1
                yield event
                if event.get('type') in ('mission_complete','error','mission_interrupted'):
                    return
            if result['terminal']:
                yield {'type':'error','error':'Mission ended without a terminal event',
                       'error_kind':'interrupted_stream','last_event_id':last_event_id}
                return
            if not rows:
                yield {'type':'heartbeat','transport':'poll'}

    def mission_status(self, mission_id: str) -> dict:
        return self.get(f"/api/cli/mission/{mission_id}/status")

    def mission_stop(self, mission_id: str) -> dict:
        return self.post(f"/api/cli/mission/{mission_id}/stop")

    def missions_list(self) -> dict:
        return self.get("/api/cli/missions")

    def mission_evidence(self, mission_id: str, *, evidence_id: str = "", pointer: str = "", tool: str = "", offset: int = 0, limit: int = 16) -> dict:
        return self.get(f"/api/cli/mission/{mission_id}/evidence", params={
            'evidence_id':evidence_id, 'pointer':pointer, 'tool':tool, 'offset':offset, 'limit':limit})

    def mission_resume(self, mission_id: str, model: str = "") -> dict:
        return self.post(f"/api/cli/mission/{mission_id}/resume", {"model": model})
