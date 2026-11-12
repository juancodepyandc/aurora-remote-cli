"""Local MCP servers: one registry, imported from other clients, searchable.

The registry is ``<config dir>/mcp.json`` in the common ``{"mcpServers": {...}}``
format, so a file copied from Claude Desktop, Claude Code or Cursor works
as-is. Servers come from (first definition of a name wins):

1. the project: ``.mcp.json``, ``.aurora/mcp_config.json``;
2. the user registry managed by ``jobia mcp add/import/install``;
3. the historical ``~/.aurora/mcp_config.json``.

Each entry is either a local process (``command``/``args``/``env``/``cwd``) or
a remote endpoint (``url`` + ``transport`` ``http``/``sse`` + ``headers``).
``${VAR}`` and ``${VAR:-default}`` are expanded from the environment at call
time, never written back. Tool catalogs are cached for instant search and
refreshed from the live servers on demand.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from aurora_cli import fuzzy
from aurora_cli.core import locations

REGISTRY_API = "https://registry.modelcontextprotocol.io/v0/servers"
_NAME = re.compile(r"[A-Za-z0-9_.@-]{1,80}")
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
CATALOG_KINDS = {"tools": ("tools/list", "tools"), "resources": ("resources/list", "resources"),
                 "resource_templates": ("resources/templates/list", "resourceTemplates"),
                 "prompts": ("prompts/list", "prompts")}


# --- Registry file ----------------------------------------------------------

def registry_path() -> Path:
    return locations.config_dir() / "mcp.json"


def cache_path() -> Path:
    return locations.data_dir() / "mcp-catalog.json"


def _read_json(path: Path) -> Any:
    try:
        if path.suffix == ".toml":
            try:
                import tomllib
            except ModuleNotFoundError:  # Python < 3.11
                import tomli as tomllib
            return tomllib.loads(path.read_text("utf-8"))
        text = path.read_text("utf-8")
        try:
            return json.loads(text)
        except ValueError:
            # VS Code and Zed settings tolerate comments and trailing commas.
            return json.loads(_strip_jsonc(text))
    except (OSError, ValueError, UnicodeError):
        return None


def _strip_jsonc(text: str) -> str:
    out, i, quoted = [], 0, False
    while i < len(text):
        ch = text[i]
        if quoted:
            out.append(ch)
            if ch == "\\" and i + 1 < len(text):
                out.append(text[i + 1])
                i += 1
            elif ch == '"':
                quoted = False
        elif ch == '"':
            quoted = True
            out.append(ch)
        elif text.startswith("//", i):
            while i < len(text) and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        else:
            out.append(ch)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _write_private(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".mcp-", suffix=".tmp", dir=path.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, indent=2, ensure_ascii=False)
        try:
            os.chmod(tmp, 0o600)  # env values may hold tokens
        except OSError:
            pass
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _registry_file() -> dict:
    raw = _read_json(registry_path())
    return raw if isinstance(raw, dict) else {}


def load_registry() -> dict[str, dict]:
    servers = _registry_file().get("mcpServers")
    return {name: cfg for name, cfg in (servers or {}).items() if isinstance(cfg, dict)} \
        if isinstance(servers, dict) else {}


def disabled_names() -> set[str]:
    names = _registry_file().get("disabled")
    return {n for n in names if isinstance(n, str)} if isinstance(names, list) else set()


def save_registry(servers: dict[str, dict], disabled: set[str] | None = None) -> None:
    payload: dict[str, Any] = {"mcpServers": servers}
    names = disabled_names() if disabled is None else disabled
    if names:
        payload["disabled"] = sorted(names)
    _write_private(registry_path(), payload)


# --- Normalisation -------------------------------------------------------------

def normalize(cfg: dict) -> dict:
    """Validate one server entry from any supported client format."""
    if not isinstance(cfg, dict):
        raise ValueError("Configuration MCP invalide")
    url = cfg.get("url") or cfg.get("httpUrl") or cfg.get("serverUrl")
    kind = str(cfg.get("type") or cfg.get("transport") or "").lower()
    entry: dict[str, Any] = {}
    if isinstance(url, str) and url.strip() and not cfg.get("command"):
        url = url.strip()
        if not url.startswith(("http://", "https://", "${")):
            raise ValueError("L'URL MCP doit commencer par http:// ou https://")
        legacy = kind == "sse" or (not kind and "httpUrl" not in cfg and url.rstrip("/").endswith("/sse"))
        entry.update(url=url, transport="sse" if legacy else "http")
        headers = cfg.get("headers") or {}
        if not isinstance(headers, dict):
            raise ValueError("Les en-têtes MCP doivent être un objet")
        entry["headers"] = {str(k): str(v) for k, v in headers.items()}
        token_var = cfg.get("bearer_token_env_var")
        if isinstance(token_var, str) and token_var and "Authorization" not in entry["headers"]:
            entry["headers"]["Authorization"] = "Bearer ${" + token_var + "}"
    else:
        command = cfg.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("Commande MCP absente")
        args = cfg.get("args", [])
        if not isinstance(args, list):
            raise ValueError("Les arguments MCP doivent être une liste")
        env = cfg.get("env", {})
        if not isinstance(env, dict):
            raise ValueError("L'environnement MCP doit être un objet")
        entry.update(command=command.strip(), args=[str(a) for a in args],
                     env={str(k): str(v) for k, v in env.items()})
        if isinstance(cfg.get("cwd"), str) and cfg["cwd"].strip():
            entry["cwd"] = cfg["cwd"].strip()
    for flag in ("disabled", "persistent"):
        if cfg.get(flag) is True:
            entry[flag] = True
    if cfg.get("enabled") is False:
        entry["disabled"] = True
    for text in ("description", "source", "registry_name"):
        if isinstance(cfg.get(text), str) and cfg[text].strip():
            entry[text] = cfg[text].strip()[:500]
    return entry


def expand(cfg: dict, environ: dict | None = None) -> dict:
    """Resolve ``${VAR}`` placeholders for one call; the registry keeps them."""
    environ = os.environ if environ is None else environ

    def sub(value: str) -> str:
        return _VAR.sub(lambda m: environ.get(m.group(1), m.group(2) or ""), value)

    result = dict(cfg)
    for key in ("command", "url", "cwd"):
        if isinstance(result.get(key), str):
            result[key] = sub(result[key])
    if isinstance(result.get("args"), list):
        result["args"] = [sub(a) for a in result["args"]]
    for key in ("env", "headers"):
        if isinstance(result.get(key), dict):
            result[key] = {k: sub(v) for k, v in result[key].items()}
    return result


def missing_values(cfg: dict) -> list[str]:
    """Placeholders without value (``${VAR}`` unset, VS Code ``${input:…}``)."""
    text = json.dumps({k: cfg.get(k) for k in ("command", "args", "env", "url", "headers")})
    missing = [m.group(1) for m in _VAR.finditer(text) if m.group(2) is None and m.group(1) not in os.environ]
    missing += re.findall(r"\$\{input:([^}]+)\}", text)
    return sorted(set(missing))


def describe(cfg: dict) -> str:
    if cfg.get("url"):
        return f"{cfg['url']} ({'SSE' if cfg.get('transport') == 'sse' else 'HTTP'})"
    return " ".join([cfg.get("command", ""), *cfg.get("args", [])]).strip()


def fingerprint(cfg: dict) -> str:
    material = {k: cfg.get(k) for k in ("command", "args", "env", "cwd", "url", "transport", "headers")}
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()[:16]


# --- Servers in use ------------------------------------------------------------

def project_files(workspace: Path) -> list[Path]:
    return [workspace / ".mcp.json", workspace / ".aurora" / "mcp_config.json"]


def legacy_file() -> Path:
    return Path.home() / ".aurora" / "mcp_config.json"


def parse_servers(data: Any, *, workspace: Path | None = None) -> dict[str, dict]:
    """Server entries from Claude, Cursor, VS Code, Windsurf, Gemini or Codex files."""
    if not isinstance(data, dict):
        return {}
    found: dict[str, dict] = {}
    groups = [data.get("mcpServers"), data.get("servers"), data.get("mcp_servers")]
    if isinstance(data.get("mcp"), dict):
        groups.append(data["mcp"].get("servers"))
    if workspace is not None and isinstance(data.get("projects"), dict):
        project = data["projects"].get(str(workspace)) or data["projects"].get(str(workspace).replace("\\", "/"))
        if isinstance(project, dict):
            groups.append(project.get("mcpServers"))
    if not any(isinstance(g, dict) for g in groups) and all(isinstance(v, dict) for v in data.values()) \
            and any("command" in v or "url" in v for v in data.values()):
        groups.append(data)  # bare {name: cfg} files used by older Aurora configs
    for group in groups:
        if not isinstance(group, dict):
            continue
        for name, cfg in group.items():
            if not isinstance(name, str) or name in found:
                continue
            try:
                found[name] = normalize(cfg)
            except ValueError:
                continue
    return found


def servers(workspace: Path | str | None = None, *, include_disabled: bool = False) -> dict[str, dict]:
    """Every configured server visible from ``workspace``, with its origin."""
    root = Path(workspace or Path.cwd()).resolve()
    merged: dict[str, dict] = {}
    switched_off = disabled_names()
    sources = [(path, "projet") for path in project_files(root)]
    sources += [(registry_path(), "jobia"), (legacy_file(), "aurora")]
    for path, scope in sources:
        if not path.is_file():
            continue
        for name, cfg in parse_servers(_read_json(path), workspace=root).items():
            if name in merged:
                continue
            entry = {**cfg, "scope": scope, "origin": str(path)}
            if name in switched_off:
                entry["disabled"] = True
            if entry.get("disabled") and not include_disabled:
                merged[name] = None  # keeps lower-priority duplicates hidden
                continue
            merged[name] = entry
    return {name: cfg for name, cfg in merged.items() if cfg is not None}


def add(name: str, cfg: dict, *, overwrite: bool = False) -> dict:
    if not _NAME.fullmatch(name or ""):
        raise ValueError("Nom de serveur invalide (lettres, chiffres, _ . @ -)")
    entry = normalize(cfg)
    registry = load_registry()
    if name in registry and not overwrite:
        raise ValueError(f"Le serveur {name} existe déjà (--force pour remplacer)")
    registry[name] = entry
    save_registry(registry)
    return entry


def remove(name: str) -> bool:
    registry = load_registry()
    if name not in registry:
        return False
    del registry[name]
    save_registry(registry, disabled_names() - {name})
    return True


def set_enabled(name: str, enabled: bool) -> bool:
    """Switch any visible server on or off (project entries included)."""
    if name not in servers(include_disabled=True):
        return False
    registry, names = load_registry(), disabled_names()
    if name in registry and registry[name].get("disabled"):
        registry[name] = {k: v for k, v in registry[name].items() if k != "disabled"}
    if enabled:
        names.discard(name)
    else:
        names.add(name)
    save_registry(registry, names)
    return True


# --- Import from other clients ---------------------------------------------------

def _app_config_root() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support"
    if os.name == "nt":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")


def client_sources(workspace: Path | None = None) -> list[tuple[str, str, Path]]:
    """``(id, label, path)`` of MCP configurations of known clients, per OS."""
    home, apps = Path.home(), _app_config_root()
    root = Path(workspace or Path.cwd()).resolve()
    return [
        ("claude-desktop", "Claude Desktop", apps / "Claude" / "claude_desktop_config.json"),
        ("claude-code", "Claude Code", home / ".claude.json"),
        ("claude-code", "Claude Code (projet)", root / ".mcp.json"),
        ("cursor", "Cursor", home / ".cursor" / "mcp.json"),
        ("cursor", "Cursor (projet)", root / ".cursor" / "mcp.json"),
        ("vscode", "VS Code", apps / "Code" / "User" / "mcp.json"),
        ("vscode", "VS Code (réglages)", apps / "Code" / "User" / "settings.json"),
        ("vscode", "VS Code Insiders", apps / "Code - Insiders" / "User" / "mcp.json"),
        ("vscode", "VS Code (projet)", root / ".vscode" / "mcp.json"),
        ("windsurf", "Windsurf", home / ".codeium" / "windsurf" / "mcp_config.json"),
        ("gemini", "Gemini CLI", home / ".gemini" / "settings.json"),
        ("codex", "Codex CLI", home / ".codex" / "config.toml"),
        ("aurora", "Aurora (historique)", legacy_file()),
    ]


def scan_clients(workspace: Path | None = None) -> list[dict]:
    """Candidates found in other clients, marked when already registered."""
    registry = load_registry()
    root = Path(workspace or Path.cwd()).resolve()
    seen, rows = set(), []
    for client_id, label, path in client_sources(root):
        if not path.is_file() or path.resolve() == registry_path().resolve():
            continue
        for name, cfg in parse_servers(_read_json(path), workspace=root).items():
            key = (name, fingerprint(cfg))
            if key in seen:
                continue
            seen.add(key)
            current = registry.get(name)
            rows.append({"client": client_id, "label": label, "path": str(path), "name": name, "config": cfg,
                         "status": ("identique" if current and fingerprint(normalize(current)) == fingerprint(cfg)
                                    else "conflit" if current else "nouveau"),
                         "missing": missing_values(cfg)})
    return rows


def load_source(source: str, *, workspace: Path | None = None) -> dict[str, dict]:
    """Servers from a client id, a JSON/TOML file or an http(s) URL."""
    root = Path(workspace or Path.cwd()).resolve()
    known = [row for row in client_sources(root) if row[0] == source]
    if known:
        found: dict[str, dict] = {}
        for _, _, path in known:
            if path.is_file():
                for name, cfg in parse_servers(_read_json(path), workspace=root).items():
                    found.setdefault(name, cfg)
        return found
    if source.startswith(("http://", "https://")):
        import httpx
        response = httpx.get(source, timeout=20, follow_redirects=True)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Fichier MCP distant trop volumineux")
        try:
            data = response.json()
        except ValueError:
            data = json.loads(_strip_jsonc(response.text))
        return parse_servers(data, workspace=root)
    path = Path(source).expanduser()
    if path.is_dir():
        path = next((p for p in (path / ".mcp.json", path / "mcp.json") if p.is_file()), path)
    if not path.is_file():
        raise ValueError(f"Source MCP introuvable : {source}")
    data = _read_json(path)
    if data is None:
        raise ValueError(f"Fichier MCP illisible : {path}")
    return parse_servers(data, workspace=root)


def import_servers(found: dict[str, dict], *, names=None, overwrite=False, origin="") -> tuple[list[str], list[str]]:
    """Copy servers into the registry; returns ``(imported, skipped)``."""
    registry = load_registry()
    imported, skipped = [], []
    for name, cfg in found.items():
        if names and name not in names:
            continue
        if name in registry and not overwrite:
            if fingerprint(normalize(registry[name])) != fingerprint(cfg):
                skipped.append(name)
            continue
        entry = dict(cfg)
        if origin:
            entry.setdefault("source", origin)
        registry[name] = entry
        imported.append(name)
    if imported:
        save_registry(registry)
    return imported, skipped


# --- Calls -----------------------------------------------------------------------

def call(cfg: dict, method: str, params: dict | None = None, *, timeout: float = 20.0,
         cwd: str | Path | None = None):
    """``(response, error)`` for one JSON-RPC request on a configured server."""
    resolved = expand({k: v for k, v in cfg.items() if k not in ("scope", "origin")})
    if resolved.get("url"):
        from .mcp_http import call_http
        return call_http(resolved, method, params or {}, timeout=timeout)
    from .mcp_transport import call_stdio
    command = resolved.get("command", "")
    if command and not os.path.isabs(command):
        located = shutil.which(command)
        if located is None:
            return None, f"Commande introuvable : {command} (installez-la ou corrigez la configuration)"
        # Windows needs the full path to start npx.cmd / uvx.exe without a shell.
        resolved["command"] = located
    workdir = resolved.pop("cwd", None) or cwd
    return call_stdio(resolved, method, params or {}, timeout=timeout, cwd=workdir)


def close(cfg: dict, *, cwd: str | Path | None = None) -> bool:
    resolved = expand({k: v for k, v in cfg.items() if k not in ("scope", "origin")})
    if resolved.get("url"):
        from .mcp_http import close_http
        return close_http(resolved)
    from .mcp_transport import close_stdio
    return close_stdio(resolved, cwd=resolved.pop("cwd", None) or cwd)


def catalog(cfg: dict, kind: str = "tools", cursor: str | None = None, *, timeout: float = 20.0, cwd=None) -> dict:
    if kind not in CATALOG_KINDS:
        raise ValueError("Type de catalogue MCP inconnu")
    method, field = CATALOG_KINDS[kind]
    response, error = call(cfg, method, {"cursor": cursor} if cursor else {}, timeout=timeout, cwd=cwd)
    if error:
        raise RuntimeError(error)
    result = response["result"]
    items = result.get(field)
    following = result.get("nextCursor")
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ValueError("Page de catalogue MCP invalide")
    if following is not None and (not isinstance(following, str) or not following or len(following) > 4096):
        raise ValueError("Curseur MCP invalide")
    return {"kind": kind, "items": items, "next_cursor": following}


def all_items(cfg: dict, kind: str = "tools", *, timeout: float = 20.0, cwd=None, pages: int = 10) -> list[dict]:
    items, cursor, seen = [], None, set()
    for _ in range(pages):
        page = catalog(cfg, kind, cursor, timeout=timeout, cwd=cwd)
        items.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None or cursor in seen or len(items) > 1000:
            return items[:1000]
        seen.add(cursor)
    return items


# --- Catalog cache and search ------------------------------------------------------

def load_cache() -> dict:
    data = _read_json(cache_path())
    return data if isinstance(data, dict) else {}


def refresh(names=None, *, workspace=None, timeout: float = 25.0) -> dict:
    """Query live servers in parallel and cache tools, prompts and resources."""
    visible = servers(workspace)
    targets = {n: c for n, c in visible.items() if not names or n in names}
    cache = load_cache()

    def probe(item):
        name, cfg = item
        record = {"fingerprint": fingerprint(cfg),
                  "fetched_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")}
        try:
            record["tools"] = [{"name": t.get("name", ""), "description": str(t.get("description", ""))[:400],
                                "inputSchema": t.get("inputSchema", {})}
                               for t in all_items(cfg, "tools", timeout=timeout, cwd=workspace)]
        except Exception as exc:  # A broken server must not hide the others.
            record["error"] = str(exc)[:300]
            return name, record
        for kind in ("prompts", "resources"):
            try:
                record[kind] = [{"name": str(i.get("name") or i.get("uri", "")),
                                 "description": str(i.get("description", ""))[:300],
                                 **({"uri": i["uri"]} if isinstance(i.get("uri"), str) else {})}
                                for i in all_items(cfg, kind, timeout=min(timeout, 10), cwd=workspace, pages=3)]
            except Exception:
                record[kind] = []
        return name, record

    if targets:
        with ThreadPoolExecutor(max_workers=min(4, len(targets))) as pool:
            for name, record in pool.map(probe, targets.items()):
                cache[name] = record
    try:
        _write_private(cache_path(), cache)
    except OSError:
        pass
    return cache


def search(query: str, *, workspace=None, refresh_live: bool = False, limit: int = 30) -> list[dict]:
    """Rank servers, tools, prompts and resources by relevance to ``query``."""
    visible = servers(workspace, include_disabled=True)
    cache = refresh(workspace=workspace) if refresh_live else load_cache()
    rows: list[dict] = []
    for name, cfg in visible.items():
        rows.append({"kind": "serveur", "server": name, "name": name,
                     "description": cfg.get("description", "") or describe(cfg),
                     "disabled": bool(cfg.get("disabled"))})
        record = cache.get(name, {})
        for kind, label in (("tools", "outil"), ("prompts", "prompt"), ("resources", "ressource")):
            for item in record.get(kind, []):
                rows.append({"kind": label, "server": name, "name": item.get("name", ""),
                             "description": item.get("description", ""), "disabled": bool(cfg.get("disabled")),
                             **({"uri": item["uri"]} if item.get("uri") else {})})
    ranked = fuzzy.rank(query, rows, lambda r: [(r["name"], 3.0), (r["server"], 1.5), (r["description"], 1.0)],
                        limit=limit)
    return [{**row, "score": round(score, 2)} for score, row in ranked]


# --- Official MCP registry -----------------------------------------------------------

def registry_search(query: str, *, limit: int = 15, client=None) -> list[dict]:
    """Search the official MCP registry (registry.modelcontextprotocol.io)."""
    import httpx
    params = {"search": query, "limit": max(1, min(limit, 100)), "version": "latest"}
    owner = client or httpx.Client(timeout=20, follow_redirects=True)
    try:
        response = owner.get(REGISTRY_API, params=params)
        response.raise_for_status()
        payload = response.json()
    finally:
        if client is None:
            owner.close()
    rows = []
    for item in payload.get("servers", []) if isinstance(payload, dict) else []:
        server = item.get("server", item) if isinstance(item, dict) else {}
        if not isinstance(server, dict) or not isinstance(server.get("name"), str):
            continue
        meta = (item.get("_meta") or {}).get("io.modelcontextprotocol.registry/official", {}) if isinstance(item, dict) else {}
        if meta.get("status") in {"deleted", "deprecated"}:
            continue
        rows.append({"name": server["name"], "description": str(server.get("description", ""))[:300],
                     "version": server.get("version", ""), "packages": server.get("packages") or [],
                     "remotes": server.get("remotes") or [],
                     "repository": (server.get("repository") or {}).get("url", "")})
    return rows


def _arguments(entries) -> list[str]:
    args = []
    for argument in entries or []:
        if not isinstance(argument, dict):
            continue
        value = argument.get("value") or argument.get("default") or ""
        if argument.get("type") == "named" and argument.get("name"):
            args.append(str(argument["name"]))
            if value:
                args.append(str(value))
        elif value:
            args.append(str(value))
    return args


def install_config(entry: dict, *, env: dict | None = None, prefer: str = "") -> dict:
    """Configuration for a registry entry: package (npm/pypi/oci) or remote."""
    env = env or {}
    packages = [p for p in entry.get("packages", []) if isinstance(p, dict)]
    remotes = [r for r in entry.get("remotes", []) if isinstance(r, dict) and r.get("url")]
    order = {"npm": 0, "pypi": 1, "oci": 2}
    packages.sort(key=lambda p: order.get(p.get("registryType"), 9))
    if prefer == "remote" and remotes:
        packages = []
    for package in packages:
        kind, ident, version = package.get("registryType"), package.get("identifier"), package.get("version")
        transport = (package.get("transport") or {}).get("type", "stdio")
        if not ident or transport != "stdio":
            continue
        variables = {v["name"]: env.get(v["name"], "${" + v["name"] + "}")
                     for v in package.get("environmentVariables") or [] if isinstance(v, dict) and v.get("name")}
        variables.update({k: v for k, v in env.items() if k not in variables})
        runtime_args = _arguments(package.get("runtimeArguments"))
        package_args = _arguments(package.get("packageArguments"))
        if kind == "npm":
            spec = f"{ident}@{version}" if version else ident
            args = runtime_args or ["-y"]
            return {"command": "npx", "args": [*args, spec, *package_args], "env": variables,
                    "registry_name": entry.get("name", ""), "description": entry.get("description", "")}
        if kind == "pypi":
            spec = f"{ident}=={version}" if version else ident
            return {"command": "uvx", "args": [*runtime_args, spec, *package_args], "env": variables,
                    "registry_name": entry.get("name", ""), "description": entry.get("description", "")}
        if kind == "oci":
            image = f"{ident}:{version}" if version and ":" not in ident else ident
            env_flags = [flag for name in variables for flag in ("-e", name)]
            return {"command": "docker", "args": ["run", "-i", "--rm", *env_flags, *runtime_args, image, *package_args],
                    "env": variables, "registry_name": entry.get("name", ""), "description": entry.get("description", "")}
    for remote in remotes:
        kind = str(remote.get("type", "streamable-http"))
        headers = {}
        for header in remote.get("headers") or []:
            if not isinstance(header, dict) or not header.get("name"):
                continue
            name = str(header["name"])
            variable = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").upper() or "MCP_HEADER"
            template = str(header.get("value") or "")
            # Registry templates use {placeholder}; they become ${PLACEHOLDER}.
            value = (re.sub(r"\{([A-Za-z0-9_]+)\}", lambda m: "${" + m.group(1).upper() + "}", template)
                     if template else "${" + variable + "}")
            headers[name] = env.get(name) or env.get(variable) or value
        return {"url": remote["url"], "type": "sse" if kind == "sse" else "http", "headers": headers,
                "registry_name": entry.get("name", ""), "description": entry.get("description", "")}
    raise ValueError("Aucun paquet stdio ni point d'accès distant installable pour ce serveur")


def default_alias(registry_name: str) -> str:
    tail = registry_name.rsplit("/", 1)[-1]
    alias = re.sub(r"[^A-Za-z0-9_.-]+", "-", tail).strip("-") or "mcp"
    return alias[:80]
