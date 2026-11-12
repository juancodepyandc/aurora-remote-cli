"""Skills: reusable instructions in ``<name>/SKILL.md`` folders.

The format is the common one (Claude Code, Claude apps, Aurora bridge): a
folder holding ``SKILL.md`` whose frontmatter declares at least ``name`` and
``description``; scripts and references may sit beside it.

Search order (the first definition of a name wins):

* project: ``./.aurora/skills``, ``./.claude/skills``, ``../.aurora/skills``;
* user: ``~/.aurora/skills`` (where ``jobia skills import/create`` write, and
  where the Aurora bridge on the same machine also looks), ``~/.claude/skills``;
* system: ``/etc/aurora/skills`` (``%PROGRAMDATA%\\aurora\\skills`` on Windows).

Imports accept a folder, a ``.zip``/``.skill`` archive, a ``SKILL.md`` URL, or
a GitHub repository (``owner/repo``, ``https://github.com/owner/repo[/tree/branch/path]``)
holding one or many skills. Catalogs (``anthropics/skills`` by default) are
cached locally so that searching them is instant after the first download.
"""
from __future__ import annotations

import datetime
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from aurora_cli import fuzzy
from aurora_cli.core import locations

DEFAULT_CATALOGS = ("anthropics/skills",)
MAX_SKILL_BYTES = 262_144
MAX_FILE_BYTES = 10 * 1024 * 1024
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")
_SKIP = {".git", "__pycache__", "node_modules", ".venv", ".DS_Store"}


@dataclass
class Skill:
    name: str
    description: str
    path: str
    level: str
    triggers: list[str] = field(default_factory=list)
    enabled: bool = True
    source: str = ""
    license: str = ""
    size: int = 0

    @property
    def file(self) -> Path:
        return Path(self.path) / "SKILL.md"

    def to_dict(self) -> dict:
        return {**asdict(self), "file": str(self.file)}


# --- Frontmatter -------------------------------------------------------------------

def _scalar(raw: str):
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        inner = value[1:-1]
        if value[0] == "'":
            return inner.replace("''", "'")
        try:
            return json.loads(value)  # escapes like \n, \" and \u00e9; UTF-8 kept as is
        except ValueError:
            return inner
    if value.startswith("[") and value.endswith("]"):
        return [str(_scalar(item)) for item in re.split(r",(?![^\[]*\])", value[1:-1]) if item.strip()]
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    return value


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """A tolerant YAML subset: scalars, quotes, inline/dash lists, ``>``/``|`` blocks."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = next((i for i in range(1, len(lines)) if lines[i].strip() in {"---", "..."}), None)
    if end is None:
        return {}, text
    meta: dict = {}
    block = lines[1:end]
    i = 0
    while i < len(block):
        line = block[i]
        match = re.match(r"^([A-Za-z0-9_.-]+):\s*(.*)$", line)
        if not match:
            i += 1
            continue
        key, rest = match.group(1), match.group(2)
        if rest in {">", "|", ">-", "|-", ">+", "|+"}:
            chunk = []
            i += 1
            while i < len(block) and (not block[i].strip() or block[i][:1] in {" ", "\t"}):
                chunk.append(block[i].strip())
                i += 1
            meta[key] = (" ".join(c for c in chunk if c) if rest.startswith(">") else "\n".join(chunk)).strip()
            continue
        if rest == "":
            items, j = [], i + 1
            while j < len(block) and re.match(r"^\s*-\s+", block[j]):
                items.append(str(_scalar(re.sub(r"^\s*-\s+", "", block[j]))))
                j += 1
            if items:
                meta[key] = items
                i = j
                continue
            nested, j = {}, i + 1
            while j < len(block) and block[j][:1] in {" ", "\t"} and ":" in block[j]:
                sub_key, _, sub_value = block[j].strip().partition(":")
                nested[sub_key.strip()] = _scalar(sub_value)
                j += 1
            meta[key] = nested if nested else ""
            i = j
            continue
        meta[key] = _scalar(rest)
        i += 1
    return meta, "\n".join(lines[end + 1:]).lstrip("\n")


def _triggers(meta: dict) -> list[str]:
    raw = meta.get("triggers") or meta.get("keywords") or meta.get("tags") or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",")]
    return [str(item) for item in raw if str(item).strip()][:40] if isinstance(raw, list) else []


# --- Locations and state --------------------------------------------------------------

def user_library() -> Path:
    return Path.home() / ".aurora" / "skills"


def project_library(workspace: Path | None = None) -> Path:
    return Path(workspace or Path.cwd()).resolve() / ".aurora" / "skills"


def search_roots(workspace: Path | None = None) -> list[tuple[str, Path]]:
    root = Path(workspace or Path.cwd()).resolve()
    system = (Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "aurora" / "skills"
              if os.name == "nt" else Path("/etc/aurora/skills"))
    return [("projet", root / ".aurora" / "skills"), ("projet", root / ".claude" / "skills"),
            ("projet", root.parent / ".aurora" / "skills"), ("utilisateur", user_library()),
            ("claude", Path.home() / ".claude" / "skills"), ("système", system)]


def _state_path() -> Path:
    return locations.config_dir() / "skills.json"


def _state() -> dict:
    try:
        data = json.loads(_state_path().read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), "utf-8")
    os.replace(tmp, path)


def disabled_names() -> set[str]:
    names = _state().get("disabled", [])
    return {n for n in names if isinstance(n, str)} if isinstance(names, list) else set()


def load_skill(folder: Path, level: str = "utilisateur") -> Skill | None:
    file = folder / "SKILL.md"
    try:
        if not file.is_file() or file.stat().st_size > MAX_SKILL_BYTES:
            return None
        text = file.read_text("utf-8")
    except (OSError, UnicodeError):
        return None
    meta, body = parse_frontmatter(text)
    name = str(meta.get("name") or folder.name).strip()
    description = str(meta.get("description") or "").strip()
    if not description:
        first = next((line.strip("# ").strip() for line in body.splitlines() if line.strip()), "")
        description = first[:300]
    origin = ""
    marker = folder / ".jobia-skill.json"
    if marker.is_file():
        try:
            origin = str(json.loads(marker.read_text("utf-8")).get("source", ""))
        except (OSError, ValueError):
            origin = ""
    return Skill(name=name, description=description, path=str(folder), level=level,
                 triggers=_triggers(meta), source=origin, license=str(meta.get("license") or ""),
                 size=len(text.encode("utf-8")))


def _folders(base: Path, depth: int = 1):
    """Skill folders under ``base`` (direct children, or deeper for catalogs)."""
    if not base.is_dir():
        return
    stack = [(base, 0)]
    while stack:
        current, level = stack.pop()
        try:
            children = sorted(p for p in current.iterdir() if p.is_dir() and p.name not in _SKIP)
        except OSError:
            continue
        for child in children[:500]:
            if (child / "SKILL.md").is_file():
                yield child
            elif level + 1 < depth:
                stack.append((child, level + 1))


def discover(workspace: Path | None = None, *, include_disabled: bool = False) -> list[Skill]:
    found, seen = [], set()
    off = disabled_names()
    for level, base in search_roots(workspace):
        for folder in _folders(base):
            skill = load_skill(folder, level)
            if skill is None or skill.name in seen:
                continue
            seen.add(skill.name)
            skill.enabled = skill.name not in off
            if skill.enabled or include_disabled:
                found.append(skill)
    return found


def get(name: str, workspace: Path | None = None) -> Skill | None:
    wanted = name.strip().lower()
    return next((s for s in discover(workspace, include_disabled=True)
                 if s.name.lower() == wanted or Path(s.path).name.lower() == wanted), None)


def read(name: str, workspace: Path | None = None) -> tuple[Skill, str]:
    skill = get(name, workspace)
    if skill is None:
        raise ValueError(f"Skill introuvable : {name}")
    return skill, skill.file.read_text("utf-8")


def search(query: str, workspace: Path | None = None, *, skills: list[Skill] | None = None,
           limit: int = 20, with_content: bool = True) -> list[tuple[float, Skill]]:
    pool = skills if skills is not None else discover(workspace, include_disabled=True)
    bodies = {}
    if with_content:
        for skill in pool:
            try:
                bodies[skill.path] = parse_frontmatter(skill.file.read_text("utf-8"))[1][:20000]
            except (OSError, UnicodeError):
                bodies[skill.path] = ""
    return fuzzy.rank(query, pool, lambda s: [(s.name, 3.0), (s.description, 1.8), (" ".join(s.triggers), 2.0),
                                              (bodies.get(s.path, ""), 0.4)], limit=limit)


def suggest(request: str, workspace: Path | None = None, *, threshold: float = 5.0) -> Skill | None:
    """The clearly relevant enabled skill for a request, if any."""
    candidates = [s for s in discover(workspace) if s.triggers or s.description]
    best = None
    padded = f" {fuzzy.normalize(request)} "
    for skill in candidates:
        # An explicit trigger is a whole word or phrase of the request.
        hits = sum(1 for trigger in skill.triggers
                   if fuzzy.normalize(trigger) and f" {fuzzy.normalize(trigger)} " in padded)
        score = hits * 5.0 + fuzzy.score(skill.name.replace("-", " "), [(request, 1.0)])
        if score >= threshold and (best is None or score > best[0]):
            best = (score, skill)
    return best[1] if best else None


def set_enabled(name: str, enabled: bool, workspace: Path | None = None) -> bool:
    skill = get(name, workspace)
    if skill is None:
        return False
    state = _state()
    names = disabled_names()
    (names.discard if enabled else names.add)(skill.name)
    state["disabled"] = sorted(names)
    _save_state(state)
    return True


def remove(name: str, workspace: Path | None = None) -> Path:
    """Move a library skill to a dated backup; returns the backup path."""
    skill = get(name, workspace)
    if skill is None:
        raise ValueError(f"Skill introuvable : {name}")
    folder = Path(skill.path).resolve()
    allowed = [user_library().resolve(), project_library(workspace).resolve(),
               (Path(workspace or Path.cwd()).resolve() / ".claude" / "skills").resolve(),
               (Path.home() / ".claude" / "skills").resolve()]
    if not any(folder.parent == root for root in allowed):
        raise ValueError(f"Ce skill n'appartient pas à une bibliothèque modifiable : {folder}")
    backup = locations.data_dir() / "skills-trash" / f"{folder.name}-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), str(backup))
    return backup


def create(name: str, description: str, instructions: str = "", *, triggers=(), project: bool = False,
           workspace: Path | None = None, overwrite: bool = False) -> Skill:
    if not _NAME.fullmatch(name or ""):
        raise ValueError("Nom de skill invalide (lettres, chiffres, - _ .)")
    if not description.strip():
        raise ValueError("Une description est nécessaire : elle sert à retrouver le skill.")
    folder = (project_library(workspace) if project else user_library()) / name
    if folder.exists() and not overwrite:
        raise ValueError(f"Le skill {name} existe déjà : {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {json.dumps(description.strip(), ensure_ascii=False)}"]
    if triggers:
        lines.append("triggers: [" + ", ".join(json.dumps(t, ensure_ascii=False) for t in triggers) + "]")
    lines += ["---", "", instructions.strip() or f"# {name}\n\n{description.strip()}", ""]
    (folder / "SKILL.md").write_text("\n".join(lines), "utf-8")
    return load_skill(folder, "projet" if project else "utilisateur")


# --- Import ---------------------------------------------------------------------------

_GITHUB = re.compile(r"^(?:https?://github\.com/|gh:|github:)?([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?"
                     r"(?:/tree/([^/]+)(?:/(.+))?)?/?$")


def _github(source: str):
    if source.startswith(("http://", "https://")) and "github.com" not in source:
        return None
    if not source.startswith(("http://", "https://", "gh:", "github:")) and (Path(source).exists() or source.count("/") != 1):
        return None
    match = _GITHUB.match(source.strip())
    if not match:
        return None
    owner, repo, branch, subpath = match.groups()
    return owner, repo, branch or "", (subpath or "").strip("/")


def _safe_extract(archive: zipfile.ZipFile, target: Path) -> None:
    root = target.resolve()
    for member in archive.infolist():
        destination = (target / member.filename).resolve()
        if not destination.is_relative_to(root):
            raise ValueError("Archive refusée : chemin hors du dossier d'import")
        if member.file_size > MAX_FILE_BYTES:
            continue
        if member.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, open(destination, "wb") as dst:
                shutil.copyfileobj(src, dst)


def fetch_repository(owner: str, repo: str, branch: str, destination: Path) -> str:
    """Shallow clone with git when present, else the GitHub archive. Returns the revision."""
    url = f"https://github.com/{owner}/{repo}.git"
    if shutil.which("git"):
        command = ["git", "clone", "--depth", "1", "--quiet", *(["--branch", branch] if branch else []), url, str(destination)]
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
        result = subprocess.run(command, capture_output=True, text=True, timeout=300, env=env)
        if result.returncode == 0:
            revision = subprocess.run(["git", "-C", str(destination), "rev-parse", "HEAD"],
                                      capture_output=True, text=True, timeout=30)
            return revision.stdout.strip()
        shutil.rmtree(destination, ignore_errors=True)
    import httpx
    ref = f"refs/heads/{branch}" if branch else "HEAD"
    response = httpx.get(f"https://codeload.github.com/{owner}/{repo}/zip/{ref}", timeout=120, follow_redirects=True)
    response.raise_for_status()
    if len(response.content) > 200 * 1024 * 1024:
        raise ValueError("Archive trop volumineuse")
    with tempfile.TemporaryDirectory() as unpacked:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            _safe_extract(archive, Path(unpacked))
        top = next((p for p in Path(unpacked).iterdir() if p.is_dir()), None)
        if top is None:
            raise ValueError("Archive vide")
        shutil.copytree(top, destination)
    return branch or "HEAD"


def _copy_skill(source: Path, target: Path) -> None:
    def ignore(directory, names):
        skipped = set()
        for item in names:
            path = Path(directory) / item
            if item in _SKIP or path.is_symlink() or (path.is_file() and path.stat().st_size > MAX_FILE_BYTES):
                skipped.add(item)
        return skipped
    shutil.copytree(source, target, ignore=ignore)


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    available: list[str] = field(default_factory=list)
    target: str = ""


def _skill_folders(root: Path) -> list[Path]:
    if (root / "SKILL.md").is_file():
        return [root]
    return list(_folders(root, depth=5))


def import_skills(source: str, *, names=(), project: bool = False, overwrite: bool = False,
                  workspace: Path | None = None, list_only: bool = False) -> ImportReport:
    """Copy skills from ``source`` into the user (or project) library."""
    target_root = project_library(workspace) if project else user_library()
    report = ImportReport(target=str(target_root))
    wanted = {n.lower() for n in names}
    with tempfile.TemporaryDirectory(prefix="jobia-skills-") as scratch:
        scratch_path = Path(scratch)
        origin, revision = source, ""
        github = _github(source)
        local = Path(source).expanduser()
        if local.exists():
            if local.is_file() and local.suffix.lower() in {".zip", ".skill"}:
                with zipfile.ZipFile(local) as archive:
                    _safe_extract(archive, scratch_path / "archive")
                root = scratch_path / "archive"
            elif local.is_file() and local.name.upper() == "SKILL.MD":
                root = local.parent
            elif local.is_dir():
                root = local
            else:
                raise ValueError(f"Source de skill non reconnue : {source}")
            origin = str(local.resolve())
        elif github:
            owner, repo, branch, subpath = github
            checkout = scratch_path / "repo"
            revision = fetch_repository(owner, repo, branch, checkout)
            root = checkout / subpath if subpath else checkout
            if not root.exists():
                raise ValueError(f"Chemin absent du dépôt : {subpath}")
            origin = f"https://github.com/{owner}/{repo}" + (f"/tree/{branch}/{subpath}" if branch or subpath else "")
        elif source.startswith(("http://", "https://")):
            import httpx
            response = httpx.get(source, timeout=60, follow_redirects=True)
            response.raise_for_status()
            if len(response.content) > 50 * 1024 * 1024:
                raise ValueError("Téléchargement trop volumineux")
            path = urlsplit(source).path.lower()
            if path.endswith((".zip", ".skill")) or response.content[:2] == b"PK":
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    _safe_extract(archive, scratch_path / "archive")
                root = scratch_path / "archive"
            else:
                meta, _ = parse_frontmatter(response.text)
                folder_name = str(meta.get("name") or Path(urlsplit(source).path).parent.name or "skill")
                folder_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", folder_name).strip("-") or "skill"
                root = scratch_path / folder_name
                root.mkdir(parents=True)
                (root / "SKILL.md").write_text(response.text, "utf-8")
        else:
            raise ValueError(f"Source introuvable : {source} (dossier, archive, URL ou owner/repo GitHub)")
        folders = _skill_folders(root)
        if not folders:
            raise ValueError("Aucun SKILL.md trouvé dans la source")
        for folder in folders:
            skill = load_skill(folder)
            if skill is None:
                continue
            report.available.append(skill.name)
            if wanted and skill.name.lower() not in wanted and folder.name.lower() not in wanted:
                continue
            if list_only:
                continue
            if not _NAME.fullmatch(skill.name):
                report.skipped.append(skill.name)
                continue
            destination = target_root / skill.name
            if destination.exists():
                if not overwrite:
                    report.skipped.append(skill.name)
                    continue
                shutil.rmtree(destination)
            destination.parent.mkdir(parents=True, exist_ok=True)
            _copy_skill(folder, destination)
            (destination / ".jobia-skill.json").write_text(json.dumps({
                "source": origin, "revision": revision,
                "imported_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            }, ensure_ascii=False), "utf-8")
            report.imported.append(skill.name)
    return report


# --- Catalogs -------------------------------------------------------------------------

def catalogs() -> list[str]:
    try:
        from aurora_cli import config
        configured = config.load().get("skill_catalogs")
    except Exception:
        configured = None
    if isinstance(configured, list) and all(isinstance(c, str) for c in configured) and configured:
        return configured
    return list(DEFAULT_CATALOGS)


def catalog_dir(name: str) -> Path:
    return locations.data_dir() / "skill-catalogs" / name.replace("/", "__")


def sync_catalog(name: str, *, max_age: float = 86400.0, force: bool = False) -> Path:
    github = _github(name)
    if github is None:
        raise ValueError(f"Catalogue invalide : {name} (owner/repo GitHub attendu)")
    folder = catalog_dir(name)
    stamp = folder / ".jobia-synced"
    if not force and stamp.is_file() and time.time() - stamp.stat().st_mtime < max_age:
        return folder
    owner, repo, branch, _ = github
    fresh = folder.with_name(folder.name + ".new")
    shutil.rmtree(fresh, ignore_errors=True)
    fetch_repository(owner, repo, branch, fresh)
    shutil.rmtree(folder, ignore_errors=True)
    fresh.rename(folder)
    stamp.write_text(str(time.time()), "utf-8")
    return folder


def catalog_skills(name: str, *, refresh: bool = False) -> list[Skill]:
    folder = sync_catalog(name, force=refresh)
    found = []
    for skill_folder in _folders(folder, depth=5):
        skill = load_skill(skill_folder, "catalogue")
        if skill is not None:
            skill.source = name
            found.append(skill)
    return found


def catalog_search(query: str, *, refresh: bool = False, limit: int = 20, sources=None):
    rows, errors = [], []
    for name in sources or catalogs():
        try:
            rows.extend(catalog_skills(name, refresh=refresh))
        except Exception as exc:
            errors.append(f"{name} : {exc}")
    return search(query, skills=rows, limit=limit), errors


# --- Activation in a request -----------------------------------------------------------

def context_block(names, workspace: Path | None = None, *, budget: int = 24000) -> str:
    """Instructions of explicitly activated skills, bounded, for a request."""
    blocks, used = [], 0
    for name in names:
        try:
            skill, text = read(name, workspace)
        except (ValueError, OSError, UnicodeError):
            continue
        _, body = parse_frontmatter(text)
        room = max(0, min(12000, budget - used))
        if room <= 200:
            break
        excerpt = body[:room]
        if len(body) > room:
            excerpt += f"\n[… suite dans {skill.file}]"
        used += len(excerpt)
        blocks.append(f"[SKILL ACTIVÉ · {skill.name}] {skill.description}\n{excerpt}")
    return "\n\n".join(blocks)
