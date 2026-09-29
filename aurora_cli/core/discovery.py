"""Local runtime discovery.

Scans the machine for anything that can serve a model, using three
independent signals so a runtime is found even when it is installed in an
unusual way:

1. **Binaries on PATH** — cheap, no network, finds installs that are not
   running yet.
2. **HTTP probes on loopback** — finds servers started by a GUI app, a
   container, or a systemd unit, none of which are on PATH.
3. **Model files on disk** — finds weights that exist even if no runtime is
   running, so ``models`` can tell the user what they already own.

Everything is bounded: probes run concurrently with a short timeout and the
filesystem walk has a depth and file-count limit, so this never turns into a
minute-long hang on a slow disk.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import re
import socket
import time
from dataclasses import dataclass
from pathlib import Path

from aurora_cli.core import locations
from aurora_cli.core.providers import (
    LocalFlavour,
    ModelInfo,
    ProviderInfo,
    ProviderKind,
    parse_parameter_count,
    parse_quantization,
)

#: Time budget for the whole discovery pass.
DISCOVERY_TIMEOUT = 3.0

#: Per-probe timeout. Kept short: a closed loopback port fails instantly,
#: so a long timeout only slows down the ports that are filtered.
PROBE_TIMEOUT = 0.35

#: Model file extensions worth counting.
MODEL_SUFFIXES = (".gguf", ".safetensors", ".bin", ".onnx", ".pt", ".pth",
                  ".mlx", ".ggml", ".msgpack", ".h5",
                  # 3D pipelines ship their main stages as .ckpt
                  ".ckpt")

#: Files larger than this are assumed to be weights, not metadata.
MIN_MODEL_BYTES = 1_000_000

#: Cap on how many files one directory walk yields.
MAX_FILES = 4000

#: Cap on walk depth below each search root.
MAX_DEPTH = 4

#: Known runtimes: executable names, default port, flavour, human label.
KNOWN_RUNTIMES: tuple[dict, ...] = (
    {"id": "ollama", "binaries": ("ollama",), "port": 11434,
     "flavour": LocalFlavour.OLLAMA, "label": "Ollama"},
    {"id": "llamacpp", "binaries": ("llama-server", "server", "llamafile"),
     "port": 8080, "flavour": LocalFlavour.LLAMACPP, "label": "llama.cpp"},
    {"id": "lmstudio", "binaries": ("lms", "lmstudio"), "port": 1234,
     "flavour": LocalFlavour.LMSTUDIO, "label": "LM Studio"},
    {"id": "vllm", "binaries": ("vllm",), "port": 8000,
     "flavour": LocalFlavour.OPENAI, "label": "vLLM"},
    {"id": "localai", "binaries": ("local-ai", "localai"), "port": 8081,
     "flavour": LocalFlavour.OPENAI, "label": "LocalAI"},
    {"id": "koboldcpp", "binaries": ("koboldcpp", "koboldcpp.py"), "port": 5001,
     "flavour": LocalFlavour.OPENAI, "label": "KoboldCpp"},
    {"id": "jan", "binaries": ("jan",), "port": 1337,
     "flavour": LocalFlavour.OPENAI, "label": "Jan"},
    {"id": "gpt4all", "binaries": ("gpt4all",), "port": 4891,
     "flavour": LocalFlavour.OPENAI, "label": "GPT4All"},
    {"id": "llamafile", "binaries": ("llamafile-server",), "port": 8080,
     "flavour": LocalFlavour.OPENAI, "label": "llamafile"},
)

#: Additional loopback ports probed even with no matching binary, because a
#: container or a GUI app can expose an OpenAI-compatible API invisibly.
EXTRA_OPENAI_PORTS = (1234, 8000, 8080, 11434, 5001, 4891, 5000, 1337,
                      8081, 11435, 3000, 5005, 6969, 8001)

#: Extra ports to consider for Ollama, whose default can be remapped.
EXTRA_OLLAMA_PORTS = (11434, 11435, 11436)


@dataclass
class ScanResult:
    """Everything one discovery pass found."""

    providers: list[ProviderInfo]
    loose_models: list[ModelInfo]
    duration_ms: float = 0.0
    scanned_roots: int = 0
    scanned_files: int = 0

    @property
    def any_provider(self) -> bool:
        return any(p.healthy for p in self.providers)

    @property
    def total_models(self) -> int:
        served = sum(len(p.models) for p in self.providers)
        return served + len(self.loose_models)


# --- Binary discovery -----------------------------------------------------


def find_binary(names: tuple[str, ...]) -> tuple[str, str]:
    """Return ``(name, path)`` for the first match on this machine."""
    if not names:
        return "", ""
    for directory in locations.executable_dirs():
        for name in names:
            candidate = directory / name
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return name, str(candidate)
            except OSError:
                continue
    return "", ""


# --- Port probing ---------------------------------------------------------


def port_open(port: int, host: str = "127.0.0.1",
              timeout: float = PROBE_TIMEOUT) -> bool:
    """TCP connect to a loopback port with a tight timeout."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, socket.timeout):
        return False


def probe_ollama(port: int) -> ProviderInfo | None:
    """Ask an Ollama server for its tag list."""
    import httpx

    base = f"http://127.0.0.1:{port}"
    info = ProviderInfo(
        id=f"ollama:{port}", label="Ollama", kind=ProviderKind.LOCAL,
        flavour=LocalFlavour.OLLAMA, base_url=base,
    )
    started = time.monotonic()
    try:
        response = httpx.get(f"{base}/api/tags", timeout=1.5)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - discovery must never raise
        info.detail = _short_error(exc)
        info.checked_at = time.monotonic()
        return info
    info.latency_ms = (time.monotonic() - started) * 1000
    info.healthy = True
    info.checked_at = time.monotonic()
    info.capabilities = ["chat", "stream", "embeddings", "tools"]
    for entry in (payload.get("models") or []):
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or entry.get("model") or "")
        if not name:
            continue
        details = entry.get("details") or {}
        size = _as_int(details.get("size") or _deep_size(entry))
        info.models.append(ModelInfo(
            name=name,
            provider="ollama",
            size_bytes=size,
            parameter_count=parse_parameter_count(details.get("parameter_size")),
            quantization=str(details.get("quantization_level") or ""),
            family=str(details.get("family") or ""),
            modified=str(entry.get("modified_at") or ""),
            source="ollama",
        ))
    return info


def probe_openai_compatible(port: int, flavour: LocalFlavour,
                            label: str, provider_id: str) -> ProviderInfo:
    """Ask an OpenAI-compatible server for ``/v1/models``."""
    import httpx

    base = f"http://127.0.0.1:{port}"
    info = ProviderInfo(
        id=provider_id, label=label, kind=ProviderKind.LOCAL,
        flavour=flavour, base_url=base,
    )
    headers = _auth_headers()
    started = time.monotonic()
    try:
        response = httpx.get(f"{base}/v1/models", headers=headers, timeout=1.5)
        if response.status_code == 404:
            # Some servers expose only /models.
            response = httpx.get(f"{base}/models", headers=headers, timeout=1.5)
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        info.detail = _short_error(exc)
        info.checked_at = time.monotonic()
        return info
    info.latency_ms = (time.monotonic() - started) * 1000
    info.healthy = True
    info.checked_at = time.monotonic()
    info.capabilities = ["chat", "stream"]
    for entry in (payload.get("data") or payload.get("models") or []):
        name = ""
        if isinstance(entry, dict):
            name = str(entry.get("id") or entry.get("name") or entry.get("model") or "")
        elif isinstance(entry, str):
            name = entry
        if not name:
            continue
        info.models.append(ModelInfo(
            name=name, provider=provider_id,
            parameter_count=parse_parameter_count(name),
            quantization=parse_quantization(name),
            family=name.split("/")[0] if "/" in name else "",
            source=provider_id,
        ))
    return info


def _auth_headers() -> dict[str, str]:
    """Read an API key from the environment if the user set one."""
    from aurora_cli import brand

    for name in (brand.env("api_key"), brand.env_legacy("api_key")):
        value = os.environ.get(name, "").strip()
        if value:
            return {"Authorization": f"Bearer {value}"}
    return {}


def _short_error(exc: Exception) -> str:
    text = str(exc).split("\n")[0][:120]
    return text or exc.__class__.__name__


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value) -> float:
    """Parse a number that may carry a unit suffix such as ``3.0B``."""
    if isinstance(value, (int, float)):
        return float(value)
    if not value:
        return 0.0
    text = str(value).strip().upper()
    multipliers = (("B", 1e9), ("M", 1e6), ("K", 1e3))
    for suffix, factor in multipliers:
        if text.endswith(suffix):
            text = text[:-1]
            try:
                return float(text) * factor
            except ValueError:
                return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def _deep_size(entry: dict) -> int:
    """Ollama nests size under ``details`` or in a blob list depending on version."""
    for key in ("size", "size_bytes"):
        if key in entry:
            return _as_int(entry[key])
    return 0


# --- Filesystem scan ------------------------------------------------------


#: A Diffusers pipeline is a directory holding ``model_index.json``. A single
#: stage (a Hunyuan DiT, a VAE) instead ships one checkpoint beside a
#: ``config.yaml``. Both are models, and both must be recognised as a set:
#: a 3D generator without its VAE produces nothing.
PIPELINE_MARKER = "model_index.json"
STAGE_CONFIG_MARKERS = ("config.yaml", "config.json")

#: Substrings in a Diffusers ``_class_name`` that mean "this makes 3D".
_3D_CLASS_MARKERS = ("mesh", "3d", "shape", "geometry", "hunyuan3d", "triposr",
                     "trellis", "instantmesh", "imagecraft")

#: Markers of the *texturing* half of a 3D project. A Hunyuan checkout holds
#: both a shape pipeline and a paint-PBR one, and both are "3D" as far as a
#: coarse capability goes. Without this split a mesh generator looks like a
#: texturer, and the plan reports a job as ready when the required weights are
#: absent: the user asks for a textured mesh and is told nothing is needed
#: because only the generator is installed. Tested before the generic 3D
#: markers, since "paintpbr" also contains no 3D marker but "hunyuan3d" does.
_3D_TEXTURE_MARKERS = ("paintpbr", "paint_pbr", "texture", "texgen", "unetpaint",
                       "texturemap", "pbr")

#: Substrings that mean "encodes images/vision", not "generates text".
_VISION_CLASS_MARKERS = ("dinov2", "vision", "clipimage", "imageencoder",
                         "siglip", "resnet", "dinov3", "convnext")


def _classify_pipeline(class_name: str, component_names: list[str],
                       dirname: str = "") -> str:
    """Map a Diffusers class name, or a directory name, to a JOBIA capability.

    Separators are stripped before matching so one marker covers every
    spelling a real project uses: ``image_encoder``, ``image-encoder`` and
    ``ImageEncoder`` are the same component, and a marker list that only
    covers one of them silently misclassifies the model.

    Order matters: a 3D pipeline also contains vision encoders, so the 3D
    markers are tested first. Getting this backwards is how a texturing
    pipeline gets reported as a chat model.
    """
    haystack = re.sub(r"[^a-z0-9]", "", " ".join(
        [class_name, *component_names, dirname]).lower())
    for marker in _3D_TEXTURE_MARKERS:
        if marker in haystack:
            return "3d-texture"
    for marker in _3D_CLASS_MARKERS:
        if marker in haystack:
            return "3d"
    for marker in _VISION_CLASS_MARKERS:
        if marker in haystack:
            return "vision"
    return "llm"


def _dir_size(path: Path, *, budget: int = 200_000) -> int:
    """Total bytes under ``path``, stopping once ``budget`` files are seen."""
    total = 0
    seen = 0
    for dirpath, _dirnames, filenames in os.walk(path, followlinks=True):
        for name in filenames:
            seen += 1
            if seen > budget:
                return total
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def scan_pipelines(roots: list[tuple[str, Path]] | None = None,
                   *, max_depth: int = 5) -> list[ModelInfo]:
    """Find model directories and report each one as a single model.

    A 3D pipeline is a directory of a dozen weight files plus config JSON.
    Counting files individually reports a pile of anonymous ``.ckpt`` blobs and
    loses the only thing that matters — that the set is a working model, and
    that it was already on the machine. This groups them and records where they
    came from, which is what lets a later cleanup step distinguish "installed
    by us" from "already there".
    """
    pairs = roots if roots is not None else locations.model_search_roots()
    found: list[ModelInfo] = []
    seen: set[str] = set()

    for runtime, root in pairs:
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
            markers = [name for name in (PIPELINE_MARKER, *STAGE_CONFIG_MARKERS)
                       if name in filenames]
            if not markers:
                dirnames[:] = [d for d in dirnames if d not in {"blobs", ".git"}]
                continue
            try:
                depth = len(Path(dirpath).relative_to(root).parts)
            except ValueError:
                depth = 0
            if depth >= max_depth:
                continue
            model_dir = Path(dirpath)
            resolved = str(model_dir.resolve())
            if resolved in seen:
                continue
            # A config without weights is metadata (a tokenizer, a scheduler).
            # Judge on size before claiming the directory is a model.
            size = _dir_size(model_dir)
            if size < MIN_MODEL_BYTES:
                dirnames[:] = [d for d in dirnames if d not in {"blobs", ".git"}]
                continue
            seen.add(resolved)

            class_name = ""
            components: list[str] = []
            pipeline_index = model_dir / PIPELINE_MARKER
            if pipeline_index.is_file():
                try:
                    payload = json.loads(pipeline_index.read_text("utf-8", errors="replace"))
                    class_name = str(payload.get("_class_name", "") or "")
                    components = [str(key) for key in payload if not key.startswith("_")]
                except (OSError, ValueError):
                    class_name = ""

            name = model_dir.name
            parent = model_dir.parent.name
            # weights/Hunyuan3D-2.1 is the repo; the subdirs are the models.
            display = f"{parent}/{name}" if parent and parent not in {"weights", "models"} else name
            found.append(ModelInfo(
                name=display,
                provider=runtime,
                source=runtime,
                size_bytes=size,
                family=name.split("-")[0],
                capability=_classify_pipeline(class_name, components, name),
                weight_format="diffusers" if class_name else "checkpoint",
                path=str(model_dir),
            ))
            # Do not descend into a model we already accepted: its unet/ and
            # vae/ subdirs are parts, not separate models.
            dirnames[:] = []
    return found


def _is_inside(path: str, directory: Path) -> bool:
    """True when ``path`` is a file belonging to ``directory``.

    Used to hide a pipeline's own weight files from the loose-file listing.
    """
    try:
        return os.path.commonpath([str(Path(path).resolve()),
                                   str(directory.resolve())]) == str(directory.resolve())
    except (ValueError, OSError):
        return False


def scan_model_files(roots: list[tuple[str, Path]] | None = None,
                     *, max_files: int = MAX_FILES) -> tuple[list[ModelInfo], int, int]:
    """Walk model directories and report the weights found on disk.

    Returns ``(models, roots_scanned, files_seen)``. Depth and file count are
    bounded so a multi-terabyte cache cannot stall startup.
    """
    pairs = roots if roots is not None else locations.model_search_roots()
    found: list[ModelInfo] = []
    seen: set[str] = set()
    files_seen = 0
    roots_scanned = 0

    for runtime, root in pairs:
        if files_seen >= max_files:
            break
        try:
            if not root.is_dir():
                continue
        except OSError:
            continue
        roots_scanned += 1
        stack: list[tuple[Path, int]] = [(root, 0)]
        # Hugging Face stores weights as symlinks from snapshots/ into blobs/,
        # and some tools link whole directories. Walking real links is what
        # makes those caches visible at all, so symlink loops have to be
        # excluded explicitly rather than by ignoring links.
        visited: set[tuple[int, int]] = set()
        while stack:
            current, depth = stack.pop()
            if depth > MAX_DEPTH or files_seen >= max_files:
                continue
            try:
                stat = current.stat()
                key = (stat.st_dev, stat.st_ino)
            except OSError:
                continue
            if key in visited:
                continue
            visited.add(key)
            try:
                children = list(os.scandir(current))
            except OSError:
                continue
            for entry in children:
                if files_seen >= max_files:
                    break
                try:
                    if entry.is_dir():
                        if not entry.name.startswith("."):
                            stack.append((Path(entry.path), depth + 1))
                    elif entry.is_file():
                        files_seen += 1
                        lowered = entry.name.lower()
                        if not lowered.endswith(MODEL_SUFFIXES):
                            continue
                        stat = entry.stat()
                        if stat.st_size < MIN_MODEL_BYTES:
                            continue
                        key = f"{runtime}:{entry.name}:{stat.st_size}"
                        if key in seen:
                            continue
                        # The same weights appear twice in a Hugging Face cache:
                        # once as the blob, once as the symlink pointing at it.
                        # Keying on the resolved path collapses those.
                        resolved = os.path.realpath(entry.path)
                        blob_key = f"{runtime}:{resolved}:{stat.st_size}"
                        if blob_key in seen:
                            continue
                        seen.add(key)
                        seen.add(blob_key)
                        found.append(_model_from_path(entry.name, stat.st_size,
                                                     str(Path(entry.path)), runtime))
                except OSError:
                    continue
    found.sort(key=lambda m: m.size_bytes, reverse=True)
    return found, roots_scanned, files_seen


#: Filenames Hugging Face uses that carry no model identity. The useful name
#: is the repository, taken from the cache directory layout instead.
_GENERIC_WEIGHT_NAMES = frozenset({
    "model", "pytorch_model", "model_weights", "consolidated", "open_clip_pytorch_model",
})


def _model_from_path(name: str, size: int, path: str, runtime: str) -> ModelInfo:
    """Derive what we can about a model from its path and filename.

    Hugging Face caches are laid out as
    ``hub/models--org--repo/snapshots/<rev>/weights``, where the filename is
    generic and the identity lives in the directory names. Everything else is
    named after the file, which is the informative case.
    """
    stem = name
    for suffix in MODEL_SUFFIXES:
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break

    identifier = stem
    # The extension is part of the identity whenever it is not already implied
    # by the name: a repo may ship both a .bin and a .safetensors copy of one
    # model, and those are different files on disk.
    weight_format = Path(name).suffix.lstrip(".").lower()
    repo = ""
    for index, part in enumerate(Path(path).parts):
        # models--org--repo / snapshots / <rev> / <file>
        if part.startswith("models--") and index + 3 < len(Path(path).parts):
            repo = part[len("models--"):].replace("--", "/")
            break
    if repo:
        identifier = repo
    elif stem.lower() in _GENERIC_WEIGHT_NAMES:
        # Name the parent directory instead of the generic file.
        identifier = Path(path).parent.name or stem

    params = parse_parameter_count(identifier)
    quant = parse_quantization(name)
    return ModelInfo(
        name=identifier,
        provider=runtime,
        size_bytes=size,
        parameter_count=params,
        quantization=quant,
        family=identifier.split("/")[-1].split("-")[0] if "-" in identifier else "",
        path=path,
        source=runtime,
        weight_format=weight_format,
    )


def scan_ollama_disk() -> list[ModelInfo]:
    """Read Ollama's on-disk manifests, so models appear even when stopped."""
    models: list[ModelInfo] = []
    manifests = locations.home() / ".ollama" / "models" / "manifests"
    if not manifests.is_dir():
        return models
    for path in sorted(manifests.rglob("*.json")):
        try:
            payload = json.loads(path.read_text("utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        parts = path.parts
        if "manifests" in parts:
            tail = list(parts[parts.index("manifests") + 1:-1])
            if len(tail) >= 2:
                name = f"{tail[0]}/{':'.join(tail[1:])}"
            elif tail:
                name = tail[0]
            else:
                continue
            models.append(ModelInfo(
                name=name, provider="ollama",
                parameter_count=parse_parameter_count(payload.get("parameter_size")),
                quantization=str(payload.get("quantization_level") or ""),
                family=str(payload.get("family") or ""),
                path=str(path), source="ollama (disque)",
            ))
    return models


# --- Custom endpoints from configuration ----------------------------------


def custom_endpoints() -> list[ProviderInfo]:
    """Providers the user declared explicitly, via config or environment."""
    from aurora_cli import brand, config as config_module

    providers: list[ProviderInfo] = []
    try:
        stored = config_module.load()
    except Exception:  # noqa: BLE001
        stored = {}
    declared = stored.get("local_endpoints") or []
    if isinstance(declared, list):
        for entry in declared:
            if not isinstance(entry, dict):
                continue
            url = str(entry.get("base_url") or "").strip()
            if not url:
                continue
            providers.append(ProviderInfo(
                id=str(entry.get("id") or url),
                label=str(entry.get("label") or entry.get("id") or "Personnalisé"),
                kind=ProviderKind.CUSTOM,
                flavour=LocalFlavour.OPENAI,
                base_url=url.rstrip("/"),
                api_key_env=str(entry.get("api_key_env") or ""),
                detail=str(entry.get("detail") or "déclaré dans la configuration"),
            ))
    env_url = os.environ.get(brand.env("base_url"), "").strip()
    if env_url:
        providers.append(ProviderInfo(
            id="env", label=brand.env("base_url"), kind=ProviderKind.CUSTOM,
            flavour=LocalFlavour.OPENAI, base_url=env_url.rstrip("/"),
            api_key_env=brand.env("api_key"),
            detail="déclaré par variable d'environnement",
        ))
    return providers


# --- Orchestration --------------------------------------------------------


def scan(*, deep: bool = True, include_files: bool = True,
         timeout: float = DISCOVERY_TIMEOUT) -> ScanResult:
    """Run a full discovery pass.

    ``deep=False`` skips the filesystem walk, which is the slow part, and is
    what the REPL calls on every startup.
    """
    started = time.monotonic()
    providers: list[ProviderInfo] = []
    seen_ids: set[str] = set()

    # 1. Binaries: proves a runtime is installed, running or not.
    for runtime in KNOWN_RUNTIMES:
        name, path = find_binary(locations.binary_names(*runtime["binaries"]))
        if not path:
            continue
        info = ProviderInfo(
            id=f"binaire:{runtime['id']}", label=runtime["label"],
            kind=ProviderKind.LOCAL, flavour=runtime["flavour"],
            binary=name, binary_path=path,
            base_url=f"http://127.0.0.1:{runtime['port']}",
            detail="installé, serveur non démarré",
        )
        if info.id not in seen_ids:
            seen_ids.add(info.id)
            providers.append(info)

    # 2. HTTP probes, in parallel, bounded by the timeout.
    jobs: list[tuple[int, LocalFlavour, str, str]] = []
    for port in EXTRA_OLLAMA_PORTS:
        jobs.append((port, LocalFlavour.OLLAMA, "Ollama", f"ollama:{port}"))
    for runtime in KNOWN_RUNTIMES:
        if runtime["flavour"] is LocalFlavour.OLLAMA:
            continue
        jobs.append((runtime["port"], runtime["flavour"], runtime["label"],
                     f"openai:{runtime['id']}"))
    for port in EXTRA_OPENAI_PORTS:
        if any(job[0] == port for job in jobs):
            continue
        jobs.append((port, LocalFlavour.UNKNOWN, f"OpenAI-compatible :{port}",
                     f"openai:{port}"))

    open_ports: list[tuple[int, LocalFlavour, str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
        futures = {pool.submit(port_open, job[0]): job for job in jobs}
        for future in concurrent.futures.as_completed(futures, timeout=max(timeout, 1.0)):
            if future.result():
                open_ports.append(futures[future])
    open_ports.sort(key=lambda job: (job[1] is not LocalFlavour.OLLAMA, job[0]))

    if open_ports:
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(8, len(open_ports))
        ) as pool:
            futures = {pool.submit(_probe, job): job for job in open_ports}
            for future in concurrent.futures.as_completed(futures):
                try:
                    info = future.result()
                except Exception:  # noqa: BLE001
                    continue
                if info is not None and info.id not in seen_ids:
                    seen_ids.add(info.id)
                    providers.append(info)

    # 3. Declared endpoints.
    for info in custom_endpoints():
        if info.id in seen_ids:
            continue
        seen_ids.add(info.id)
        started_probe = time.monotonic()
        try:
            import httpx
            headers = _auth_headers()
            response = httpx.get(f"{info.base_url}/v1/models", headers=headers, timeout=1.5)
            info.healthy = response.status_code < 500
            info.latency_ms = (time.monotonic() - started_probe) * 1000
            if info.healthy and response.status_code == 200:
                payload = response.json()
                for entry in (payload.get("data") or []):
                    if isinstance(entry, dict) and entry.get("id"):
                        info.models.append(ModelInfo(
                            name=str(entry["id"]), provider=info.id,
                            parameter_count=parse_parameter_count(str(entry["id"])),
                            quantization=parse_quantization(str(entry["id"])),
                            source=info.id,
                        ))
                info.capabilities = ["chat", "stream"]
            else:
                info.detail = f"HTTP {response.status_code}"
        except Exception as exc:  # noqa: BLE001
            info.detail = _short_error(exc)
        info.checked_at = time.monotonic()
        providers.append(info)

    # 4. Weights on disk.
    loose: list[ModelInfo] = []
    roots_scanned = 0
    files_seen = 0
    if include_files and deep:
        # Pipelines first: a Diffusers directory owns its weight files, and
        # listing those files again as anonymous blobs would both duplicate
        # the gigabytes and hide the fact that the set is a working model.
        pipelines = scan_pipelines()
        loose.extend(pipelines)
        covered_dirs = [Path(p.path) for p in pipelines]
        loose_files, roots_scanned, files_seen = scan_model_files()
        for model in loose_files:
            if any(_is_inside(model.path, directory) for directory in covered_dirs):
                continue
            loose.append(model)
        for model in scan_ollama_disk():
            loose.append(model)

    return ScanResult(
        providers=providers,
        loose_models=loose,
        duration_ms=(time.monotonic() - started) * 1000,
        scanned_roots=roots_scanned,
        scanned_files=files_seen,
    )


def _probe(job: tuple[int, LocalFlavour, str, str]) -> ProviderInfo | None:
    port, flavour, label, provider_id = job
    if flavour is LocalFlavour.OLLAMA:
        return probe_ollama(port)
    return probe_openai_compatible(port, flavour, label, provider_id)
