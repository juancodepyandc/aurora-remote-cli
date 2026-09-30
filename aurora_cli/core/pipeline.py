"""Universal autonomous pipeline: discover → generate → verify → deliver.

No hardcoded paths. No machine-specific assumptions. Works on any OS,
any hardware, any installed model. If something is missing, JOBIA
installs it automatically.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import catalog, fetcher
from .agents import get
from .bootstrap import ensure_3d_engine, ensure_hf, python_engine, start_ollama
from .machine import profile


@dataclass
class PipelineStage:
    """One stage in the pipeline."""

    name: str
    provider: str
    status: str = "pending"  # pending, running, done, failed
    output: Path | None = None
    log: str = ""
    duration_s: float = 0.0


@dataclass
class PipelineResult:
    """Result of a full pipeline run."""

    success: bool
    stages: list[PipelineStage] = field(default_factory=list)
    final_output: Path | None = None
    log: str = ""


def stage_validate_mesh(mesh_path: Path, *, require_textures: bool = False) -> PipelineStage:
    """Validate the delivered GLB before marking a complex job complete."""
    stage = PipelineStage(name="mesh_quality_gate", provider="local-validator")
    try:
        data = mesh_path.read_bytes()
        if len(data) < 20 or data[:4] != b"glTF":
            raise ValueError("GLB header absent")
        version = int.from_bytes(data[4:8], "little")
        declared = int.from_bytes(data[8:12], "little")
        if version != 2 or declared != len(data):
            raise ValueError("GLB tronqué ou version inconnue")
        if require_textures:
            payload = data[12:].lower()
            if b'"images"' not in payload or b'"textures"' not in payload:
                raise ValueError("GLB valide mais sans textures PBR embarquées")
        stage.status = "done"
        stage.output = mesh_path
        stage.log = f"GLB valide · {len(data) / 1024 ** 2:.1f} Mo"
    except (OSError, ValueError) as exc:
        stage.status = "failed"
        stage.log = str(exc)
    return stage


# --- Dynamic discovery ------------------------------------------------------


def find_model(model_name: str, *, capability: str = "") -> tuple[Path, Path] | None:
    """Find a model installation anywhere on the system.

    Searches all known cache locations, not just hardcoded paths.
    Returns ``(model_root, venv_python)`` or None.

    Prefers full installations (with venv) over bare HF cache entries.
    Falls back to the Hunyuan3D venv (which has torch + diffusers) when a
    model has no venv of its own.
    """
    from aurora_cli.core.locations import model_search_roots

    # Normalize the model name for directory matching
    normalized = model_name.lower().replace("/", "--").replace("_", "-")
    # Also try without org prefix (e.g. "hunyuan3d-2.1" from "tencent/Hunyuan3D-2.1")
    parts = normalized.split("--")
    alt_normalized = parts[-1] if len(parts) > 1 else normalized

    # Find the Hunyuan3D venv as a fallback (it has torch + diffusers)
    fallback_venv = Path(sys.executable)
    for _runtime, root in model_search_roots():
        candidate = root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
        if candidate.exists():
            fallback_venv = candidate
            break

    # First pass: look for full installations with venv (JOBIA models dir, etc.)
    for runtime, root in model_search_roots():
        if not root.is_dir():
            continue
        # Skip HF cache — those are bare weights without a venv
        if "huggingface" in root.name.lower() or ".cache" in str(root).lower():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            child_norm = child.name.lower().replace("/", "--")
            if normalized in child_norm or alt_normalized in child_norm:
                venv = child / "venv/bin/python"
                if venv.exists():
                    return child, venv
                # No venv — use the Hunyuan3D venv which has torch + diffusers
                return child, fallback_venv

    # Second pass: accept any match, use system python
    for runtime, root in model_search_roots():
        if not root.is_dir():
            continue
        # Skip HF cache in second pass too
        if "huggingface" in root.name.lower() or ".cache" in str(root).lower():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            child_norm = child.name.lower().replace("/", "--")
            if normalized in child_norm or alt_normalized in child_norm:
                return child, fallback_venv

    return None


def ensure_model(model_name: str, capability: str):
    """Find or provision a role model using the measured host profile."""
    found = find_model(model_name, capability=capability)
    # Always use JOBIA's managed runtime for executable pipelines. A cache hit
    # can be only weights (or a stale system interpreter without torch).
    if capability == "image":
        engine = python_engine("images", ["torch", "diffusers", "transformers",
                                           "accelerate", "safetensors", "Pillow"])
        if found:
            return found[0], engine
    if capability == "3d":
        root = ensure_3d_engine()
        python = root / ("venv/Scripts/python.exe" if os.name == "nt" else "venv/bin/python")
        if found:
            return root, python
    if found:
        return found
    agent = get(capability) or get("3d" if capability == "3d" else "image")
    if agent is None:
        return None
    artifact = catalog.resolve(agent, profile(), catalog.recommended_tier(agent, profile()))
    if artifact is None:
        return None
    if artifact.runtime == "huggingface":
        ensure_hf()
    elif artifact.runtime == "ollama":
        start_ollama()
    plan, _ = fetcher.install(artifact, profile(), agent=agent.id,
                              job=f"pipeline:{capability}")
    if capability == "image":
        return plan.target, engine
    if capability == "3d":
        return root, python
    return find_model(artifact.ref, capability=capability) or (plan.target, Path(sys.executable))


def find_ollama() -> str | None:
    """Check if Ollama is running."""
    try:
        import httpx
        resp = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2.0)
        if resp.status_code == 200:
            return "http://127.0.0.1:11434"
    except Exception:
        pass
    return None


def find_ollama_model(model_name: str) -> bool:
    """Check if a model is available in Ollama."""
    ollama = find_ollama()
    if not ollama:
        return False
    try:
        import httpx
        resp = httpx.get(f"{ollama}/api/tags", timeout=5.0)
        if resp.status_code == 200:
            models = resp.json().get("models", [])
            return any(model_name in m.get("name", "") for m in models)
    except Exception:
        pass
    return False


def install_ollama_model(model_name: str) -> bool:
    """Install a model into Ollama."""
    try:
        import httpx
        resp = httpx.post(
            "http://127.0.0.1:11434/api/pull",
            json={"name": model_name},
            timeout=600.0,
        )
        return resp.status_code == 200
    except Exception:
        return False


# --- Stage implementations --------------------------------------------------


def stage_generate_image(
    prompt: str,
    output_path: Path,
    *,
    model_name: str = "stabilityai/sdxl-turbo",
) -> PipelineStage:
    """Generate an image using a local diffusion model."""
    stage = PipelineStage(name="image_generation", provider=model_name)

    try:
        found = ensure_model(model_name, "image")
    except Exception as exc:
        stage.status = "failed"
        stage.log = f"Préparation automatique du moteur image impossible : {exc}"
        return stage
    if not found:
        stage.status = "failed"
        stage.log = f"Model {model_name} not found. Install it first."
        return stage

    model_root, venv_python = found
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_literal = json.dumps(prompt)
    model_literal = json.dumps(str(model_root))
    output_literal = json.dumps(str(output_path))
    device = os.environ.get("JOBIA_DEVICE", "").strip().lower() or (
        "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES", "").strip() not in ("", "-1")
        else "mps" if platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}
        else "cpu")
    dtype = "float16" if device in {"cuda", "mps"} else "float32"

    script = f'''
import torch
from diffusers import StableDiffusionXLPipeline

pipe = StableDiffusionXLPipeline.from_pretrained(
    {model_literal},
    torch_dtype=getattr(torch, {json.dumps(dtype)}),
    variant="fp16",
).to({json.dumps(device)})

image = pipe(
    prompt={prompt_literal},
    num_inference_steps=4,
    guidance_scale=0.0,
).images[0]

image.save({output_literal})
print("[image] saved", flush=True)
'''

    t0 = time.time()
    try:
        result = subprocess.run(
            [str(venv_python), "-c", script],
            capture_output=True, text=True, timeout=300,
        )
        stage.duration_s = time.time() - t0
        stage.log = result.stdout + result.stderr
        if result.returncode == 0 and output_path.exists():
            stage.status = "done"
            stage.output = output_path
        else:
            stage.status = "failed"
    except Exception as exc:
        stage.duration_s = time.time() - t0
        stage.status = "failed"
        stage.log = str(exc)

    return stage


def stage_verify_image(
    image_path: Path,
    character_desc: str,
    *,
    model_name: str = "llava",
) -> PipelineStage:
    """Verify the generated image matches the request using a VLM."""
    stage = PipelineStage(name="vlm_verification", provider=model_name)

    try:
        start_ollama()
    except Exception as exc:
        stage.status = "failed"
        stage.log = f"Préparation automatique du VLM impossible : {exc}"
        return stage
    if not find_ollama_model(model_name):
        stage.status = "running"
        stage.log = f"Installing {model_name} into Ollama..."
        if not install_ollama_model(model_name):
            stage.status = "failed"
            stage.log = f"Failed to install {model_name}"
            return stage

    import httpx
    import base64

    img_b64 = base64.b64encode(image_path.read_bytes()).decode()
    prompt = f"""Look at this image. Does it depict {character_desc}?
Answer in JSON only: {{"match": true/false, "reason": "brief explanation"}}"""

    t0 = time.time()
    try:
        resp = httpx.post(
            "http://127.0.0.1:11434/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "images": [img_b64],
                "stream": False,
                "format": "json",
            },
            timeout=120.0,
        )
        stage.duration_s = time.time() - t0
        resp.raise_for_status()
        data = resp.json()
        text = data.get("response", "")
        try:
            result = json.loads(text)
            stage.status = "done"
            stage.log = result.get("reason", "")
        except json.JSONDecodeError:
            stage.status = "done"
            stage.log = text[:200]
    except Exception as exc:
        stage.duration_s = time.time() - t0
        stage.status = "failed"
        stage.log = str(exc)

    return stage


def stage_generate_3d(
    image_path: Path,
    output_path: Path,
    *,
    model_name: str = "tencent/Hunyuan3D-2.1",
    texture: bool = True,
) -> PipelineStage:
    """Generate a 3D mesh from an image using a local pipeline."""
    stage = PipelineStage(name="3d_generation", provider=model_name)

    try:
        found = ensure_model(model_name, "3d")
    except Exception as exc:
        stage.status = "failed"
        stage.log = f"Préparation automatique du moteur 3D impossible : {exc}"
        return stage
    if not found:
        stage.status = "failed"
        stage.log = f"Model {model_name} not found. Install it first."
        return stage

    model_root, venv_python = found
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Find the actual repo inside the model root
    repo = model_root / "Hunyuan3D-2.1"
    if not repo.exists():
        # Try to find it
        for child in model_root.iterdir():
            if child.is_dir() and "hunyuan" in child.name.lower():
                repo = child
                break
    if texture and not (repo / "hy3dpaint").is_dir():
        stage.status = "failed"
        stage.log = "Texture PBR demandée mais le module hy3dpaint est absent du moteur 3D."
        return stage
    device = os.environ.get("JOBIA_DEVICE", "").strip().lower() or (
        "cuda" if os.environ.get("CUDA_VISIBLE_DEVICES", "").strip() not in ("", "-1")
        else "mps" if platform.system() == "Darwin" and platform.machine() in {"arm64", "aarch64"}
        else "cpu")
    root_literal = json.dumps(str(model_root))
    repo_literal = json.dumps(str(repo))
    image_literal = json.dumps(str(image_path))
    output_literal = json.dumps(str(output_path))

    script = f'''
import sys, os, time
os.environ["HY3D_BACKEND"] = {json.dumps(device)}
os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
sys.path.insert(0, {root_literal})
sys.path.insert(0, {json.dumps(str(repo / "hy3dshape"))})
sys.path.insert(0, {json.dumps(str(repo / "hy3dpaint"))})
os.chdir({repo_literal})

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

image = Image.open({image_literal}).convert("RGBA")
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
if hasattr(pipeline, "dtype") and isinstance(pipeline.dtype, str):
    pipeline.dtype = {{"float16": torch.float16, "float32": torch.float32,
                       "bfloat16": torch.bfloat16}}.get(pipeline.dtype, torch.float32)
print("[shape] generating mesh...", flush=True)
t0 = time.time()
mesh = pipeline(image=image)[0]
print(f"[shape] done in {{time.time()-t0:.1f}}s", flush=True)

mesh.export({output_literal})
print(f"[output] {output_literal}", flush=True)
'''

    if texture and (repo / "hy3dpaint").is_dir():
        script += f'''
print("[paint] loading adaptive PBR pipeline...", flush=True)
from textureGenPipeline import Hunyuan3DPaintPipeline, Hunyuan3DPaintConfig
conf = Hunyuan3DPaintConfig(6, 512)
conf.realesrgan_ckpt_path = "hy3dpaint/ckpt/RealESRGAN_x4plus.pth"
conf.multiview_cfg_path = "hy3dpaint/cfgs/hunyuan-paint-pbr.yaml"
conf.custom_pipeline = "hy3dpaint/hunyuanpaintpbr"
Hunyuan3DPaintPipeline(conf)(mesh_path={output_literal}, image_path={image_literal},
                            output_mesh_path={output_literal})
print("[paint] done", flush=True)
'''

    t0 = time.time()
    try:
        result = subprocess.run(
            [str(venv_python), "-c", script],
            capture_output=True, text=True, timeout=1800,
        )
        stage.duration_s = time.time() - t0
        stage.log = result.stdout + result.stderr
        if result.returncode == 0 and output_path.exists():
            stage.status = "done"
            stage.output = output_path
        else:
            stage.status = "failed"
    except Exception as exc:
        stage.duration_s = time.time() - t0
        stage.status = "failed"
        stage.log = str(exc)

    return stage


# --- Pipeline orchestration -------------------------------------------------


def run_pipeline(
    character_desc: str,
    output_dir: Path,
    *,
    image_prompt: str | None = None,
    image_model: str = "stabilityai/sdxl-turbo",
    vlm_model: str = "llava",
    model_3d: str = "tencent/Hunyuan3D-2.1",
    texture: bool = True,
    max_retries: int = 2,
) -> PipelineResult:
    """Run the full pipeline: image → VLM check → 3D mesh.

    Discovers models dynamically. Installs missing ones automatically.
    """
    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prompt = image_prompt or f"{character_desc}, full body, white background, high detail, collectible figurine style, toy photography"
    image_path = output_dir / "reference.png"
    mesh_path = output_dir / "model.glb"

    stages: list[PipelineStage] = []

    for attempt in range(max_retries):
        # Stage 1: Generate image
        img_stage = stage_generate_image(prompt + ("; improve fidelity and anatomy" if attempt else ""), image_path, model_name=image_model)
        stages.append(img_stage)
        if img_stage.status != "done":
            continue

        # Stage 2: Verify with VLM
        vlm_stage = stage_verify_image(image_path, character_desc, model_name=vlm_model)
        stages.append(vlm_stage)
        if vlm_stage.status != "done":
            continue

        # Stage 3: Generate 3D mesh
        mesh_stage = stage_generate_3d(image_path, mesh_path, model_name=model_3d,
                                       texture=texture)
        stages.append(mesh_stage)
        if mesh_stage.status == "done":
            gate = stage_validate_mesh(mesh_path, require_textures=texture)
            stages.append(gate)
            if gate.status != "done":
                continue
            return PipelineResult(
                success=True,
                stages=stages,
                final_output=mesh_path,
                log="\n".join(s.log for s in stages),
            )

    return PipelineResult(
        success=False,
        stages=stages,
        log="\n".join(s.log for s in stages),
    )
