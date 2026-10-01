"""Discover complete 3D runtimes and run them through one isolated worker."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess


@dataclass(frozen=True)
class Engine3D:
    name: str
    root: Path
    venv_python: Path
    repo: Path
    can_paint: bool

    @property
    def available(self) -> bool:
        if not self.venv_python.is_file():
            return False
        if (self.repo / 'hy3dshape').is_dir():
            return True
        import json
        try:
            receipt = json.loads((self.root / 'runtime.json').read_text())
            return (receipt['signature']['model'] == self.name
                    and (self.repo / 'trellis2').is_dir() and Path(receipt['weights']).is_dir())
        except (OSError, ValueError, KeyError, TypeError):
            return False


def discover_engines() -> list[Engine3D]:
    from .locations import data_dir, model_search_roots
    candidates: list[Path] = [data_dir() / "models" / "hunyuan3d-2.1"]
    explicit = os.environ.get("JOBIA_3D_ENGINE")
    if explicit:
        candidates.insert(0, Path(explicit).expanduser())
    for _runtime, root in model_search_roots():
        try:
            if root.is_dir():
                candidates.append(root)
                candidates.extend(p for p in root.iterdir() if p.is_dir())
        except OSError:
            continue
    engines: dict[str, Engine3D] = {}
    explicit = os.environ.get("JOBIA_3D_ENGINE")
    explicit_path = Path(explicit).expanduser() if explicit else None
    for root in candidates:
        if not root.is_dir():
            continue
        python = root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
        repo = root / "Hunyuan3D-2.1"
        if (root / "hy3dshape").is_dir():
            repo = root
        if python.is_file() and (repo / "hy3dshape").is_dir():
            engines[str(root.resolve())] = Engine3D(
                "Hunyuan3D 2.1", root, python, repo, (repo / "hy3dpaint").is_dir())
    explicit_path = Path(os.environ["JOBIA_3D_ENGINE"]).expanduser() if os.environ.get("JOBIA_3D_ENGINE") else None
    return sorted(engines.values(), key=lambda e: (
        0 if explicit_path and e.root == explicit_path else 1,
        0 if (e.root / "backend.py").is_file() and (e.root / "paint.py").is_file() else 1))


def find_hunyuan3d() -> Engine3D | None:
    """Return the first discovered Hunyuan3D engine, or None if none found."""
    return next(iter(discover_engines()), None)


def generate_mesh(engine: Engine3D, image_path: Path, output_path: Path, *,
                  paint: bool = True, timeout: int = 7200,
                  model_ref: str = "tencent/Hunyuan3D-2.1") -> tuple[bool, str]:
    """Generate a 3D mesh from an image using the given engine.

    Args:
        engine: The 3D engine to use for generation
        image_path: Path to the input image
        output_path: Path where the generated mesh will be saved
        paint: Apply PBR texturing. On by default, because a mesh without
            albedo/metallic/roughness maps is not a deliverable asset.
        timeout: Maximum time in seconds to wait for generation
        model_ref: Model reference to use for generation

    Returns:
        Tuple of (success: bool, message: str)
    """
    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    from aurora_cli.adapters import AdapterRegistry

    adapters = AdapterRegistry()
    runner, reason = adapters.resolve("3d", model_ref)
    if runner is None:
        return False, reason
    from .machine import profile
    if reason := runner.incompatibility(profile(), require_textures=paint):
        return False, reason
    if paint and not engine.can_paint:
        return False, ('Texturation demandée, mais aucun moteur de texture exécutable '
                       'n’est présent. Aucune substitution silencieuse par une forme nue.')

    try:
        command = adapters.build(
            runner, interpreter=engine.venv_python,
            worker=Path(__file__).with_name(runner.worker or "mesh_worker.py"),
            root=engine.root.resolve(), repo=engine.repo.resolve(),
            image=image_path.resolve(), output=output_path, model=model_ref,
            paint_flag="--paint" if paint else "",
        )
    except KeyError as exc:
        return False, str(exc)

    log_path = output_path.with_suffix(".runtime.log")
    try:
        with log_path.open("w", encoding="utf-8") as log:
            result = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT,
                text=True, timeout=timeout, env=os.environ | runner.environment(),
            )
        detail = log_path.read_text(encoding="utf-8", errors="replace")
        produced = output_path.is_file() and output_path.stat().st_size > 20
        return result.returncode == 0 and produced, detail
    except subprocess.TimeoutExpired:
        return False, f"Délai de calcul dépassé ; étapes et diagnostic conservés dans {log_path}."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{exc} ; diagnostic : {log_path}"


def bridge_script() -> Path:
    return Path(__file__).parent.parent / "tools" / "generate_3d.py"
