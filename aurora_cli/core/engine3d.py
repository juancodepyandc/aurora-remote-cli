"""Local execution engine for 3D generation.

Detects whether Hunyuan3D is installed and can be invoked headlessly,
so JOBIA can offer to actually run the generation instead of just
recommending weights.
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Engine3D:
    """A locally-installed 3D generation pipeline."""

    name: str
    root: Path
    venv_python: Path
    repo: Path
    can_paint: bool

    @property
    def available(self) -> bool:
        return self.venv_python.exists() and self.repo.exists()


def find_hunyuan3d() -> Engine3D | None:
    """Locate a local Hunyuan3D installation."""
    from .locations import data_dir, model_search_roots
    candidates = [data_dir() / "models" / "hunyuan3d-2.1"]
    for _runtime, root in model_search_roots():
        if root.is_dir():
            candidates.append(root)
            candidates.extend(p for p in root.iterdir() if p.is_dir())
    for root in candidates:
        venv = root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
        repo = root / "Hunyuan3D-2.1"
        if not repo.exists() and (root / "hy3dshape").is_dir():
            repo = root
        if venv.exists() and repo.is_dir():
            return Engine3D(
                name="Hunyuan3D 2.1",
                root=root,
                venv_python=venv,
                repo=repo,
                can_paint=(repo / "hy3dpaint").is_dir(),
            )
    return None


def generate_mesh(
    engine: Engine3D,
    image_path: Path,
    output_path: Path,
    *,
    paint: bool = False,
    timeout: int = 1800,
) -> tuple[bool, str]:
    """Run Hunyuan3D headlessly to generate a mesh from an image.

    Returns ``(success, log)``.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if paint and not engine.can_paint:
        return False, "Texture PBR demandée mais le module hy3dpaint est absent du moteur 3D."

    device = os.environ.get("JOBIA_DEVICE", "").strip().lower()
    if not device:
        device = "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES", "").strip() not in ("", "-1") else "cpu"
        if platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}:
            device = "mps"
    script = f'''
import sys, os, time
os.environ["HY3D_BACKEND"] = "{device}"
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")
sys.path.insert(0, "{engine.root}")
sys.path.insert(0, "{engine.repo}/hy3dshape")
sys.path.insert(0, "{engine.repo}/hy3dpaint")
os.chdir("{engine.repo}")

# Patch: Hunyuan3D calls .to(dtype="float16") with a string, which PyTorch
# rejects. Convert strings to torch.dtype before the pipeline loads.
import torch
_orig_to = torch.nn.Module.to
def _patched_to(self, *args, **kwargs):
    if "dtype" in kwargs and isinstance(kwargs["dtype"], str):
        kwargs["dtype"] = {{"float16": torch.float16, "float32": torch.float32,
                           "bfloat16": torch.bfloat16}}.get(kwargs["dtype"], torch.float32)
    args = list(args)
    for i, arg in enumerate(args):
        if isinstance(arg, str) and arg in ("float16", "float32", "bfloat16"):
            args[i] = {{"float16": torch.float16, "float32": torch.float32,
                        "bfloat16": torch.bfloat16}}[arg]
    return _orig_to(self, *args, **kwargs)
torch.nn.Module.to = _patched_to

import backend as backend_mod
import compat_patches
BACKEND = backend_mod.detect()
compat_patches.apply(BACKEND)
print(f"[init] {{backend_mod.describe(BACKEND)}}", flush=True)

from PIL import Image
from hy3dshape.rembg import BackgroundRemover
from hy3dshape.pipelines import Hunyuan3DDiTFlowMatchingPipeline

image = Image.open("{image_path}").convert("RGBA")
if image.mode == "RGB":
    rembg = BackgroundRemover()
    image = rembg(image)

print("[shape] loading DiT pipeline...", flush=True)
pipeline = Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(
    "tencent/Hunyuan3D-2.1",
    subfolder="hunyuan3d-dit-v2-1",
    use_safetensors=False,
    variant="fp16",
    device="{device}",
    dtype="float16",
)
# self.dtype is a string; convert it so .to(self.dtype) works on MPS.
if hasattr(pipeline, "dtype") and isinstance(pipeline.dtype, str):
    pipeline.dtype = {{"float16": torch.float16, "float32": torch.float32,
                       "bfloat16": torch.bfloat16}}.get(pipeline.dtype, torch.float32)
print("[shape] generating mesh...", flush=True)
t0 = time.time()
mesh = pipeline(image=image)[0]
print(f"[shape] done in {{time.time()-t0:.1f}}s", flush=True)

mesh.export("{output_path}")
print(f"[output] {output_path}", flush=True)
'''

    if paint and engine.can_paint:
        script += f'''
print("[paint] loading PBR pipeline...", flush=True)
from textureGenPipeline import Hunyuan3DPaintPipeline, Hunyuan3DPaintConfig
conf = Hunyuan3DPaintConfig(6, 512)
conf.realesrgan_ckpt_path = "hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
conf.multiview_cfg_path = "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
conf.custom_pipeline = "hy3dpaint/hunyuanpaintpbr"
paint = Hunyuan3DPaintPipeline(conf)
print("[paint] texturing...", flush=True)
paint(mesh_path="{output_path}", image_path="{image_path}", output_mesh_path="{output_path}")
print("[paint] done", flush=True)
'''

    try:
        result = subprocess.run(
            [str(engine.venv_python), "-c", script],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "Timeout après 30 min"
    except Exception as exc:
        return False, str(exc)

    log = result.stdout + result.stderr
    return result.returncode == 0, log


def bridge_script() -> Path:
    """Path to the standalone 3D generation script."""
    return Path(__file__).parent.parent / "tools" / "generate_3d.py"
