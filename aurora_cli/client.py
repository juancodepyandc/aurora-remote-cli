"""Aurora HTTP/SSE client — talks to the bridge server."""
from __future__ import annotations
import json
from typing import Any, Generator

import httpx

from aurora_cli import config


class AuroraClient:
    """Lightweight HTTP client for the Aurora bridge server."""


    def __init__(self, server_url: str = "", api_key: str = "", timeout: float = 30.0):
        cfg = config.load()
        self.server_url = (server_url or cfg.get("server_url", "")).rstrip("/")
        self.api_key = api_key or cfg.get("api_key", "")
        self.timeout = timeout
        
        # Expert mode: Robust connection transport with retries
        transport = httpx.HTTPTransport(retries=3)
        self._client = httpx.Client(
            transport=transport,
            base_url=self.server_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=httpx.Timeout(timeout, connect=10.0),
            follow_redirects=True,
        )


    def close(self) -> None:
        self._client.close()

    # --- Low-level ---

    def get(self, path: str, **kwargs: Any) -> dict:
        try:
            r = self._client.get(path, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.RequestError as e:
            return {"ok": False, "error": f"Connection error: {e}"}
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP error {e.response.status_code}"}

    def post(self, path: str, data: dict | None = None, **kwargs: Any) -> dict:
        try:
            r = self._client.post(path, json=data or {}, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.RequestError as e:
            return {"ok": False, "error": f"Connection error: {e}"}
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP error {e.response.status_code}"}

    def delete(self, path: str, **kwargs: Any) -> dict:
        try:
            r = self._client.delete(path, **kwargs)
            r.raise_for_status()
            return r.json()
        except httpx.RequestError as e:
            return {"ok": False, "error": f"Connection error: {e}"}
        except httpx.HTTPStatusError as e:
            return {"ok": False, "error": f"HTTP error {e.response.status_code}"}

    def stream_sse(self, path: str, data: dict | None = None, method: str = "POST") -> Generator[dict, None, None]:
        """Stream SSE events from the server. Yields parsed JSON dicts."""
        transport = httpx.HTTPTransport(retries=3)
        try:
            with httpx.Client(
                transport=transport,
                base_url=self.server_url,
                headers={"Authorization": f"Bearer {self.api_key}"},
                timeout=httpx.Timeout(600.0, connect=10.0),
            ) as client:
                if method == "POST":
                    req = client.stream("POST", path, json=data or {})
                else:
                    req = client.stream("GET", path)
                with req as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if line.startswith("data: "):
                            try:
                                yield json.loads(line[6:])
                            except json.JSONDecodeError:
                                continue
        except httpx.RequestError as e:
            yield {"type": "error", "error": f"Connection error: {e}"}
        except httpx.HTTPStatusError as e:
            yield {"type": "error", "error": f"HTTP error {e.response.status_code}"}

    # --- Auth ---

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
        return self.get("/api/cli/doctor")

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

    def agent_dynamic_create(self, **kwargs: Any) -> dict:
        return self.post("/api/cli/agents/dynamic/create", kwargs)

    def agent_dynamic_delete(self, agent_id: str) -> dict:
        return self.delete(f"/api/cli/agents/dynamic/{agent_id}")

    def agent_dynamic_save(self, agent_id: str) -> dict:
        return self.post(f"/api/cli/agents/dynamic/{agent_id}/save")

    # --- MCP ---

    def mcp_list(self, workspace: str = "") -> dict:
        return self.get("/api/cli/mcp/list", params={"workspace": workspace} if workspace else {})

    def mcp_tools(self, workspace: str = "") -> dict:
        return self.get("/api/cli/mcp/tools", params={"workspace": workspace} if workspace else {})

    def mcp_call(self, server: str, tool: str, arguments: dict | None = None) -> dict:
        return self.post("/api/cli/mcp/call", {"server": server, "tool": tool, "arguments": arguments or {}})

    # --- Skills ---

    def skills_list(self, workspace: str = "") -> dict:
        return self.get("/api/cli/skills/list", params={"workspace": workspace} if workspace else {})

    def skills_read(self, name: str) -> dict:
        return self.post("/api/cli/skills/read", {"name": name})

    def skills_create(self, name: str, description: str = "", level: str = "user", triggers: list[str] | None = None) -> dict:
        return self.post("/api/cli/skills/create", {"name": name, "description": description, "level": level, "triggers": triggers or []})

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

    def mission_start(self, request: str, workspace: str = "", permissions: str = "AUTONOMOUS",
                      session_id: str = "", model: str = "") -> dict:
        return self.post("/api/cli/mission/start", {
            "request": request, "workspace": workspace, "permissions": permissions,
            "session_id": session_id, "model": model,
        })

    def mission_stream(self, mission_id: str) -> Generator[dict, None, None]:
        yield from self.stream_sse(f"/api/cli/mission/{mission_id}/stream", method="GET")

    def mission_status(self, mission_id: str) -> dict:
        return self.get(f"/api/cli/mission/{mission_id}/status")

    def mission_stop(self, mission_id: str) -> dict:
        return self.post(f"/api/cli/mission/{mission_id}/stop")
