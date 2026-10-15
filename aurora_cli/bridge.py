"""HTTP and SSE client for the remote bridge."""
from __future__ import annotations

import json
import time
import base64
import hashlib
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


class Bridge:
    """HTTP and SSE client for the remote bridge server.

    Every path is relative to the configured ``server_url``; the bridge owns
    all inference, so this class never loads a model itself.
    """


    def __init__(self, server_url: str = "", api_key: str = "", timeout: float = 30.0):
        cfg = config.load()
        self.server_url = config.resolve_server_url(server_url, cfg)
        self.api_key = api_key or config.get("api_key", "")
        self.timeout = timeout
        self._mission_hosts: dict[str, str] = {}
        self._discoverable = (not server_url and not os.environ.get(brand.env('server_url'))
                              and not os.environ.get(brand.env_legacy('server_url'))
                              and (urlsplit(self.server_url).hostname or '').endswith('.trycloudflare.com'))
        
        # Expert mode: Robust connection transport with retries
        transport = httpx.HTTPTransport(retries=3)
        self._client = httpx.Client(
            transport=transport,
            base_url=self.server_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(timeout, connect=min(10.0,timeout)),
            follow_redirects=True,
        )


    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # --- Low-level ---

    def get(self, path: str, **kwargs: Any) -> dict:
        result = self._get_once(path, **kwargs)
        # Shared, pre-dispatch metadata only. Accepted mission IDs, downloads,
        # streams and all writes remain bound to their original host.
        if path in {'/api/cli/doctor', '/api/cli/status', '/api/cli/version',
                    '/api/cli/tools', '/api/cli/models'}:
            return self._refresh_metadata(path, result, **kwargs)
        return result

    def _mission_path(self, path: str) -> str:
        prefix = '/api/cli/mission/'
        if path.startswith(prefix):
            mid = path[len(prefix):].split('/', 1)[0]
            if mid in self._mission_hosts:
                return self._mission_hosts[mid].rstrip('/') + path
        return path

    def _get_once(self, path: str, **kwargs: Any) -> dict:
        try:
            r = self._client.get(self._mission_path(path), **kwargs)
            r.raise_for_status()
            return _decode_json(r)
        except httpx.RequestError as e:
            return _request_failure(e, "GET", path, kwargs.get("timeout", self._client.timeout))
        except httpx.HTTPStatusError as e:
            payload = _decode_json(e.response)
            payload.update(ok=False, http_status=e.response.status_code)
            payload.setdefault("error", f"HTTP error {e.response.status_code}")
            return payload

    def post(self, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        try:
            dispatch_host = self.server_url
            target = (dispatch_host.rstrip('/') + path if path == '/api/cli/mission/start'
                      else self._mission_path(path))
            r = self._client.post(target, json=data or {}, **kwargs)
            r.raise_for_status()
            result = _decode_json(r)
            mid = result.get('mission_id')
            if path == '/api/cli/mission/start' and result.get('ok') and isinstance(mid, str) and mid and '/' not in mid:
                self._mission_hosts[mid] = dispatch_host
            return result
        except httpx.RequestError as e:
            return _request_failure(e, "POST", path, kwargs.get("timeout", self._client.timeout))
        except httpx.HTTPStatusError as e:
            payload = _decode_json(e.response)
            payload.update(ok=False, http_status=e.response.status_code)
            payload.setdefault("error", f"HTTP error {e.response.status_code}")
            return payload

    def delete(self, path: str, **kwargs: Any) -> dict:
        try:
            r = self._client.delete(self._mission_path(path), **kwargs)
            r.raise_for_status()
            return _decode_json(r)
        except httpx.RequestError as e:
            return _request_failure(e, "DELETE", path, kwargs.get("timeout", self._client.timeout))
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP error {e.response.status_code}"}

    def stream_sse(self, path: str, data: dict | None = None, method: str = "POST",
                   *, resume: bool = False, last_event_id: int = 0) -> Generator[dict, None, None]:
        """Resume mission GET streams from complete events, never replay POSTs."""
        if resume and method != "GET":
            raise ValueError("Only mission GET streams support replay")
        if type(last_event_id) is not int or last_event_id < 0:
            raise ValueError("Mission cursor must be a non-negative integer")
        retries = 0
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
            if not resume or retries >= 3:
                yield {"type": "error", "error": error, "error_kind": error_kind,
                       "last_event_id": last_event_id}
                return
            retries += 1
            yield {"type": "reconnecting", "attempt": retries,
                   "last_event_id": last_event_id, "message": error, "error_kind": error_kind}
            time.sleep(min(2 ** (retries - 1), 4))

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

    def _refresh_metadata(self, path: str, result: dict, **kwargs: Any) -> dict:
        unavailable = result.get('error_kind') in {'connection', 'timeout'} or result.get('http_status') in {502, 503, 504, 530}
        if not self._discoverable or not unavailable:
            return result
        from .connect import discover_tunnel
        try:
            refreshed = discover_tunnel()
            if refreshed == self.server_url:
                return result
            with Bridge(server_url=refreshed, api_key=self.api_key, timeout=self.timeout) as candidate:
                diagnostic = candidate._get_once('/api/cli/doctor')
            if not diagnostic.get('ok'):
                return result
            config.set_key('server_url', refreshed)
            self._client.base_url = refreshed
            self.server_url = refreshed
            return diagnostic if path == '/api/cli/doctor' else self._get_once(path, **kwargs)
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            return result

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

    def mcp_call(self, server: str, tool: str, arguments: dict | None = None, workspace: str = "") -> dict:
        payload: dict[str, Any] = {"server": server, "tool": tool, "arguments": arguments or {}}
        if workspace:
            payload["workspace"] = workspace
        return self.post("/api/cli/mcp/call", payload)

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
        payload = {
            "request": request, "workspace": workspace, "permissions": permissions,
            "session_id": session_id, "model": model,
            "idempotency_key": idempotency_key,
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
        while True:
            result = self.get(f'/api/cli/mission/{mission_id}/events',
                              headers={'Last-Event-ID':str(last_event_id)},
                              params={'wait':20}, timeout=30)
            if not result.get('ok'):
                status = result.get('http_status')
                retryable = status in (500,502,503,504,530) or result.get('error_kind') in ('timeout','connection')
                if not retryable or retries >= 3:
                    yield {'type':'error','error':result.get('error','Mission polling failed; update the bridge'),
                           'error_kind':result.get('error_kind','http'),'last_event_id':last_event_id}
                    return
                retries += 1
                yield {'type':'reconnecting','attempt':retries,'last_event_id':last_event_id,
                       'message':result.get('error','Mission polling unavailable')}
                time.sleep(min(2 ** (retries-1),4))
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

    def mission_resume(self, mission_id: str, model: str = "") -> dict:
        return self.post(f"/api/cli/mission/{mission_id}/resume", {"model": model})
