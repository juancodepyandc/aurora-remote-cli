#!/usr/bin/env python3
"""Headless 3D generation via Hunyuan3D 2.1.

Usage:
    python generate_3d.py --image input.png --output mesh.glb [--paint]

Requires the Hunyuan3D venv at ~/.local/share/jobia/models/hunyuan3d-2.1-mac-rocm/venv.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HUNYUAN_ROOT = Path.home() / ".local/share/jobia/models/hunyuan3d-2.1-mac-rocm"
HUNYUAN_VENV = HUNYUAN_ROOT / "venv/bin/python"
HUNYUAN_REPO = HUNYUAN_ROOT / "Hunyuan3D-2.1"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a 3D mesh from an image")
    parser.add_argument("--image", required=True, help="Input image (PNG/JPG)")
    parser.add_argument("--output", required=True, help="Output .glb path")
    parser.add_argument("--paint", action="store_true", help="Apply PBR texturing")
    args = parser.parse_args()

    if not HUNYUAN_VENV.exists():
        print(f"ERROR: Hunyuan3D venv not found at {HUNYUAN_VENV}", file=sys.stderr)
        return 1

    image_path = Path(args.image).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if not image_path.exists():
        print(f"ERROR: image not found: {image_path}", file=sys.stderr)
        return 1

    script = f'''
import sys, os, time
os.environ["HY3D_BACKEND"] = "mps"
sys.path.insert(0, "{HUNYUAN_ROOT}")
sys.path.insert(0, "{HUNYUAN_REPO}/hy3dshape")
sys.path.insert(0, "{HUNYUAN_REPO}/hy3dpaint")
os.chdir("{HUNYUAN_REPO}")

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
    device="mps",
    dtype="float16",
)
if hasattr(pipeline, "dtype") and isinstance(pipeline.dtype, str):
    pipeline.dtype = {{"float16": torch.float16, "float32": torch.float32,
                       "bfloat16": torch.bfloat16}}.get(pipeline.dtype, torch.float32)
print("[shape] generating mesh...", flush=True)
t0 = time.time()
mesh = pipeline(image=image)[0]
print(f"[shape] done in {{time.time()-t0:.1f}}s", flush=True)

mesh.export("{output_path}")
print(f"[output] {{output_path}}", flush=True)
'''

    if args.paint:
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

    import subprocess
    result = subprocess.run(
        [str(HUNYUAN_VENV), "-c", script],
        capture_output=True, text=True, timeout=1800
    )
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr[-2000:], file=sys.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
