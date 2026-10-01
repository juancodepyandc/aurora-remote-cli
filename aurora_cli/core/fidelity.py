"""Measure reference/render similarity in an isolated engine worker.

Silhouette overlap and optional local CLIP embeddings are diagnostic signals,
not proof of the requested identity or of correct geometry on unseen surfaces.
No acceptance threshold is implied by the uncalibrated blended score.
The CLI can import this module without importing its numerical dependencies.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

# Verdict thresholds. Read from policies/quality.toml when available so the bar
# is an explicit policy decision, not a constant buried here. These defaults are
# only the fallback for running the module standalone.
DEFAULT_THRESHOLDS = {
    # A solid object fills a large part of its bounding box; a flattened sheet
    # encloses nothing. The armchair reconstruction measured 0.04.
    "min_bbox_fill": 0.05,
    # Best-view silhouette overlap. Tuned by inspection, not fitted to a dataset.
    "min_silhouette_iou": 0.35,
}

DEFAULT_VIEWS = ((0.0, 0.0), (45.0, 0.0), (-45.0, 0.0), (0.0, 30.0), (90.0, 0.0), (180.0, 0.0))


def camera_matrices(azimuth_deg: float, elevation_deg: float, distance: float,
                    fov_deg: float = 35.0, aspect: float = 1.0):
    """Look-at plus perspective, returning clip-space vertices for the rasterizer.

    Written out rather than pulled from a matrix library so the convention is
    auditable: the rasterizer's `_screen_coords` expects clip space with w > 0,
    which is what this produces.
    """
    import numpy as np

    if not all(math.isfinite(x) for x in (azimuth_deg, elevation_deg, distance, fov_deg, aspect)):
        raise ValueError("camera parameters must be finite")
    if distance <= 0 or not 0 < fov_deg < 180 or aspect <= 0:
        raise ValueError("invalid camera distance, field of view or aspect")

    az, el = math.radians(azimuth_deg), math.radians(elevation_deg)
    # Camera sits on the sphere around the object, looking back at the origin.
    eye = np.array([
        distance * math.cos(el) * math.sin(az),
        distance * math.sin(el),
        distance * math.cos(el) * math.cos(az),
    ], dtype=np.float32)

    forward = -eye / (np.linalg.norm(eye) + 1e-8)
    up_hint = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    if abs(float(np.dot(forward, up_hint))) > 0.999:
        up_hint = np.array([0.0, 0.0, 1.0], dtype=np.float32)
    right = np.cross(forward, up_hint)
    right /= np.linalg.norm(right) + 1e-8
    up = np.cross(right, forward)

    view = np.eye(4, dtype=np.float32)
    view[0, :3], view[1, :3], view[2, :3] = right, up, -forward
    view[:3, 3] = -view[:3, :3] @ eye

    f = 1.0 / math.tan(math.radians(fov_deg) / 2.0)
    near, far = min(0.01, distance * 1e-4), max(100.0, distance * 4)
    proj = np.zeros((4, 4), dtype=np.float32)
    proj[0, 0] = f / aspect
    proj[1, 1] = f
    # Right-handed clip space, w > 0 in front of the camera.
    proj[2, 2] = (far + near) / (near - far)
    proj[2, 3] = (2 * far * near) / (near - far)
    proj[3, 2] = -1.0
    return view, proj


def clip_positions(vertices, view, proj):
    """(N,3) world vertices -> (1,N,4) clip space, matching `torch_rasterizer`."""
    import numpy as np

    homo = np.concatenate([vertices, np.ones((vertices.shape[0], 1), dtype=np.float64)], axis=1)
    # Keep this tiny camera contraction off BLAS: some Apple builds emit false
    # overflow warnings for an otherwise finite projection. Still verify output.
    camera = np.einsum('ij,jk->ik', proj.astype(np.float64), view.astype(np.float64), optimize=False)
    clip = np.einsum('ij,nj->ni', camera, homo, optimize=False)[None].astype(np.float32)
    if not np.isfinite(clip).all():
        raise ValueError("projection produced non-finite clip coordinates")
    return clip


def _ndc_extent(clip) -> float:
    """Largest |x| and |y| in normalised device coordinates."""
    import numpy as np

    w = clip[0, :, 3]
    if np.any(w <= 0):
        raise ValueError("geometry is behind the camera")
    ndc_x = clip[0, :, 0] / w
    ndc_y = clip[0, :, 1] / w
    return float(max(np.abs(ndc_x).max(), np.abs(ndc_y).max()))


def fit_camera(vertices, azimuth, elevation, fov_deg=35.0, fill=0.80, iterations=3):
    """Solve the perspective inequalities for every vertex, including depth."""
    import numpy as np

    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not len(vertices) or not np.isfinite(vertices).all():
        raise ValueError("camera requires finite nonempty 3D vertices")
    if not 0 < fill < 1:
        raise ValueError("camera fill must be between zero and one")
    rotation = camera_matrices(azimuth, elevation, 1.0, fov_deg)[0][:3, :3]
    points = np.einsum('nj,ij->ni', vertices, rotation, optimize=False)
    if not np.isfinite(points).all():
        raise ValueError('camera rotation produced non-finite coordinates')
    extent = np.maximum(np.abs(points[:, 0]), np.abs(points[:, 1]))
    distance = max(float((points[:, 2] + extent / (math.tan(math.radians(fov_deg) / 2) * fill)).max()),
                   float(points[:, 2].max()) + 0.01)
    return camera_matrices(azimuth, elevation, distance, fov_deg)


def load_rasterizer(engine_root: Path | None = None):
    """Use an installed rasteriser or the independent bounded CPU reference."""
    import importlib

    if engine_root:
        root = str(Path(engine_root))
        if root not in sys.path:
            sys.path.insert(0, root)
    try:
        return importlib.import_module("torch_rasterizer")
    except ImportError:
        from aurora_cli.core import portable_rasterizer
        return portable_rasterizer


def _scene_parts(mesh):
    """Yield every geometry instance with its scene transform applied."""
    import trimesh

    if isinstance(mesh, trimesh.Scene):
        parts = []
        for node in mesh.graph.nodes_geometry:
            transform, name = mesh.graph[node]
            part = mesh.geometry[name].copy()
            if not isinstance(part, trimesh.Trimesh):
                continue
            part.apply_transform(transform)
            parts.append(part)
        return parts
    if isinstance(mesh, trimesh.Trimesh):
        return [mesh]
    raise ValueError("rendering requires a mesh or a mesh scene")


def render_view(mesh, azimuth: float, elevation: float, size: int = 256, *, engine_root=None,
                allow_empty_surface=False):
    """Render all instances with shared depth, perspective UVs and albedo."""
    import numpy as np
    import torch

    raster = load_rasterizer(engine_root)

    if not isinstance(size, int) or not 16 <= size <= 2048:
        raise ValueError("render size must be between 16 and 2048 pixels")
    parts = [part for part in _scene_parts(mesh) if len(part.vertices) and len(part.faces)]
    if not parts:
        raise ValueError("mesh has no geometry to render")
    all_verts, all_faces, offset = [], [], 0
    for part in parts:
        vertices, faces = np.asarray(part.vertices), np.asarray(part.faces)
        if not np.isfinite(vertices).all() or np.any(faces < 0) or np.any(faces >= len(vertices)):
            raise ValueError("mesh contains invalid vertices or faces")
        all_verts.append(vertices)
        all_faces.append(faces + offset)
        offset += len(vertices)
    verts = np.concatenate(all_verts).astype(np.float32)
    faces = np.concatenate(all_faces).astype(np.int64)
    centre = (verts.min(axis=0) + verts.max(axis=0)) / 2
    radius = float(np.linalg.norm(verts - centre, axis=1).max())
    if radius <= 1e-12:
        raise ValueError("mesh has degenerate bounds")
    verts = (verts - centre) / radius

    view, proj = fit_camera(verts, azimuth, elevation)
    clip = clip_positions(verts, view, proj)

    findices, bary = raster.rasterize(
        torch.from_numpy(clip), torch.from_numpy(faces), (size, size),
    )

    indices = findices.cpu().numpy() - 1
    bary = bary.cpu().numpy()
    mask = indices >= 0
    if not mask.any() and not allow_empty_surface:
        raise ValueError("rasterizer produced no visible surface")
    rgb = np.ones((size, size, 3), dtype=np.float32)
    face_start = 0
    for part in parts:
        ys, xs = np.nonzero((indices >= face_start) & (indices < face_start + len(part.faces)))
        if not len(ys):
            face_start += len(part.faces)
            continue
        tri = np.asarray(part.faces)[indices[ys, xs] - face_start]
        weights = bary[ys, xs]
        texture, uvs = _texture_and_uvs(part)
        visual = part.visual
        material = getattr(visual, "material", None)
        factor = getattr(material, "baseColorFactor", None)
        factor = np.asarray(factor if factor is not None else [255, 255, 255, 255], dtype=np.float32)[:3]
        if factor.max() > 1:
            factor /= 255.0
        if texture is not None and uvs is not None:
            if uvs.shape != (len(part.vertices), 2) or not np.isfinite(uvs).all():
                raise ValueError("mesh UV coordinates are invalid")
            uv = (uvs[tri] * weights[:, :, None]).sum(axis=1)
            uv = np.where((uv >= -1e-6) & (uv <= 1 + 1e-6), np.clip(uv, 0, 1), uv % 1)
            # Trimesh converts glTF top-left UVs into its own bottom-left
            # convention on import. Sample the *in-memory* convention, not the
            # serialized glTF one, to avoid reading the opposite atlas islands.
            rows = np.rint((1 - uv[:, 1]) * (texture.shape[0] - 1)).astype(np.int32)
            cols = np.rint(uv[:, 0] * (texture.shape[1] - 1)).astype(np.int32)
            rgb[ys, xs] = texture[rows, cols] / 255.0 * factor
        elif getattr(visual, "kind", "") == "vertex":
            colors = np.asarray(visual.vertex_colors, dtype=np.float32)[:, :3]
            rgb[ys, xs] = (colors[tri] * weights[:, :, None]).sum(axis=1) / 255.0
        elif getattr(visual, "kind", "") == "face":
            rgb[ys, xs] = np.asarray(visual.face_colors)[indices[ys, xs] - face_start, :3] / 255.0
        else:
            rgb[ys, xs] = factor if material is not None else 0.6
        face_start += len(part.faces)

    # The engine rasterizer has +Y down in its screen array, while our camera
    # and glTF have +Y up. Flip the framebuffer, not the object's coordinates.
    return (np.clip(rgb[::-1] * 255.0, 0, 255).astype(np.uint8), mask[::-1].copy())


def _texture_and_uvs(mesh):
    """Return (H,W,3 float array indexed [v,u], per-vertex uv) or (None, None).

    trimesh is inconsistent here: a GLB albedo arrives as a bare PIL image, a
    .obj one as a TextureVisuals wrapping an image. Both are accepted. A real
    failure to read the texture is reported rather than silently degrading to
    untextured grey, which would look like a fidelity result.
    """
    import numpy as np

    visual = getattr(mesh, "visual", None)
    uv = getattr(visual, "uv", None)
    material = getattr(visual, "material", None)
    if uv is None or material is None:
        return None, None
    texture = getattr(material, "baseColorTexture", None)
    if texture is None:
        texture = getattr(material, "image", None)
    if texture is None:
        return None, None
    image = getattr(texture, "image", texture)
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    return array, np.asarray(uv, dtype=np.float32)


def foreground_mask(image, background_tolerance: int = 18):
    """Alpha if present, otherwise pixels that differ from the border colour.

    Studio shots have a flat backdrop, so the corner colour is a usable estimate
    of the background. A synthetic fixture instead carries real alpha.
    """
    import numpy as np

    if "A" in image.getbands():
        alpha = np.asarray(image.getchannel("A"))
        if np.any(alpha < 248):
            return alpha > 8
    array = np.asarray(image.convert("RGB"), dtype=np.int16)
    corner = np.stack([array[0, 0], array[0, -1], array[-1, 0], array[-1, -1]])
    background = np.median(corner, axis=0)
    distance = np.abs(array - background).max(axis=2)
    return distance > background_tolerance


def silhouette_iou(reference_mask, render_mask) -> float:
    """Overlap of two foreground masks. 1.0 identical, 0.0 disjoint."""

    import numpy as np

    if reference_mask.shape != render_mask.shape or reference_mask.ndim != 2:
        raise ValueError("silhouette masks must have identical two-dimensional shapes")
    inter = int(np.logical_and(reference_mask, render_mask).sum())
    union = int(np.logical_or(reference_mask, render_mask).sum())
    return inter / union if union else 0.0


def bbox_fill(mesh) -> float:
    """Mesh volume as a fraction of its bounding box.

    A closed box scores 1.0. Signed volume is not a valid degeneracy test for
    arbitrary open or non-manifold surfaces; the adapter's geometry contract
    determines whether this signal is applicable. It remains a delivery gate
    for the existing volumetric Hunyuan reconstruction recipe.
    """
    import numpy as np

    try:
        extent = np.asarray(mesh.bounds, dtype=float)
    except Exception:
        return 0.0
    if extent.ndim != 2 or extent.shape[0] != 2:
        return 0.0
    box = float(np.prod(np.maximum(extent[1] - extent[0], 0.0)))
    if box <= 0:
        return 0.0
    try:
        volume = float(mesh.volume)
    except Exception:
        return 0.0
    return abs(volume) / box


def _processor_dir(encoder: Path) -> Path:
    """Where the image preprocessor config lives for this encoder directory."""
    if (encoder / "preprocessor_config.json").is_file():
        return encoder
    for name in ("feature_extractor", "preprocessor"):
        candidate = encoder.parent / name
        if (candidate / "preprocessor_config.json").is_file():
            return candidate
    raise FileNotFoundError(
        f"no preprocessor_config.json for {encoder}; expected one inside it or in a "
        f"sibling feature_extractor/ directory (the Hunyuan paint weights use the latter)"
    )


class ClipEmbedder:
    """Local CLIP vision tower, loaded once. No network, no token.

    The processor config lives in a sibling `feature_extractor/` directory in the
    Hunyuan paint weights, so both layouts are resolved: a self-contained
    directory, and a bare vision tower with its processor beside it.
    """

    def __init__(self, path: Path, device="cpu"):
        import torch
        from transformers import CLIPImageProcessor, CLIPVisionModelWithProjection

        self.torch = torch
        path = Path(path)
        if not path.is_dir():
            raise ValueError("CLIP encoder must be an existing local directory")
        self.processor = CLIPImageProcessor.from_pretrained(str(_processor_dir(path)))
        self.model = CLIPVisionModelWithProjection.from_pretrained(str(path))
        self.model.eval()
        self.device = device
        self.model.to(device)

    def embed(self, pil_images):
        import torch

        batch = self.processor(images=list(pil_images), return_tensors="pt")
        batch = {k: v.to(self.device) for k, v in batch.items()}
        with torch.inference_mode():
            features = self.model(**batch).image_embeds
        return torch.nn.functional.normalize(features, dim=-1).cpu().numpy()


def verdict(silhouette: float, fill: float, thresholds: dict,
            placement: dict | None = None) -> str:
    """Classify from measured signals, never from a blended guess.

    There is deliberately no weighted composite score here. A raw CLIP cosine is
    not on a 0..1 quality scale — an image scores ~1.0 against itself but ~0.27
    against a completely different real photograph, so any linear blend would
    invent a number that means nothing. The signals are reported raw and the
    verdict is a thresholded statement, with the thresholds in policy.
    """
    contract = thresholds.get('geometry_contract', 'volumetric')
    if contract not in {'volumetric', 'surface'}:
        raise ValueError('Unknown geometry contract')
    if not math.isfinite(fill) or not math.isfinite(silhouette):
        return 'invalid_measurement'
    if contract == 'volumetric' and fill < thresholds["min_bbox_fill"]:
        return "degenerate_geometry"
    if silhouette < thresholds["min_silhouette_iou"]:
        return "low_fidelity"
    # Placement is only judged when it was measured. An unmeasured signal must
    # not be silently treated as a pass.
    if placement is not None and placement.get("verdict") in {"misplaced"}:
        return "misplaced_texture"
    return "plausible"


def evaluate(reference_path: Path, mesh_path: Path, *, encoder_path: Path | None = None,
             engine_root: Path | None = None, views=DEFAULT_VIEWS, size: int = 256,
             thresholds: dict | None = None) -> dict:
    """Measure how faithfully a mesh depicts its reference image.

    Returns raw signals plus a thresholded verdict. Returns `semantic: None` when
    no local encoder was given, rather than substituting a default that would
    look like a measurement.
    """
    import numpy as np
    import trimesh
    from PIL import Image, ImageOps

    from aurora_cli.core.glb_validation import inspect_glb
    materials = inspect_glb(Path(mesh_path), decode_textures=True)
    # Trimesh exposes one UV set. Do not judge a wrongly sampled render as a
    # misplaced generated texture when the renderer lacks the glTF feature.
    for material in materials['materials']:
        if material['alpha_mode'] != 'OPAQUE':
            raise ValueError('Rendu de vérification non pris en charge pour les matériaux transparents/alpha-mask')
        colour = material['maps'].get('base_colour')
        if colour:
            transform = colour['transform']
            if (colour['texcoord'] != 0 or transform.get('offset', [0, 0]) != [0, 0]
                    or transform.get('scale', [1, 1]) != [1, 1] or transform.get('rotation', 0) != 0
                    or any(wrap != 10497 for wrap in colour['sampler'].values())):
                raise ValueError('Rendu de vérification non pris en charge pour ce canal/transform/sampler UV')

    views = tuple(views)
    if not views:
        raise ValueError('At least one camera view is required for visual evidence')
    thresholds = thresholds or DEFAULT_THRESHOLDS
    mesh = trimesh.load(str(mesh_path), force="scene")
    parts = _scene_parts(mesh)
    if not parts:
        raise ValueError('mesh has no visible geometry instances')
    geometry = trimesh.util.concatenate(parts)
    if not math.isfinite(float(geometry.area)) or geometry.area <= 0:
        raise ValueError('mesh has no finite nondegenerate surface')
    fill = bbox_fill(geometry)

    with Image.open(reference_path) as source:
        reference = ImageOps.exif_transpose(source).convert("RGBA")
    ref_mask = foreground_mask(reference)
    if not ref_mask.any():
        raise ValueError("reference image has no separable foreground")

    renders = [render_view(mesh, az, el, size, engine_root=engine_root,
                          allow_empty_surface=thresholds.get('geometry_contract') == 'surface')
               for az, el in views]
    images = [Image.fromarray(rgb).convert("RGB") for rgb, _ in renders]

    # The reference is whatever resolution the user shot; the renders are fixed,
    # so the mask is resampled to the render grid before the two are compared.
    mask_image = Image.fromarray((ref_mask * 255).astype(np.uint8))
    ref_mask_small = np.asarray(mask_image.resize((size, size), Image.NEAREST)) > 127
    if not ref_mask_small.any():
        raise ValueError("reference foreground vanished when resampled; lower --size is not the fix")

    # Semantic: raw CLIP cosine, plus a self-comparison control so a broken
    # encoder (returning noise, or the same vector for everything) is visible.
    semantic = control = None
    if encoder_path and Path(encoder_path).is_dir():
        embedder = ClipEmbedder(Path(encoder_path))
        ref_vector = embedder.embed([reference.convert("RGB")])[0]
        control = float(ref_vector @ ref_vector)
        semantic = float(np.mean(embedder.embed(images) @ ref_vector))

    ious = [silhouette_iou(ref_mask_small, mask) for _, mask in renders]
    silhouette = float(max(ious)) if ious else 0.0

    # Texture placement, judged on the best-matching view. Taken from the same
    # view as the silhouette score so the two signals describe one image rather
    # than two different ones.
    order = int(np.argmax(ious)) if ious else 0
    from aurora_cli.core.texture_placement import assess as assess_placement
    from aurora_cli.evolution import load_policy
    placement_policy = load_policy().get('texture_placement', {})
    placement = assess_placement(np.asarray(reference.convert("RGB"), dtype=np.float32),
        ref_mask, renders[order][0].astype(np.float32), renders[order][1],
        min_correlation=float(placement_policy.get('min_correlation', 0.60)),
        max_placement_error=float(placement_policy.get('max_placement_error', 0.35)),
        misplaced_correlation=float(placement_policy.get('misplaced_correlation', 0.20)),
        max_uniform_channel_std=float(placement_policy.get('max_uniform_channel_std', 1.0)),
        max_uniform_colour_error=float(placement_policy.get('max_uniform_colour_error', 0.05)))
    previews = []
    for index, (rgb, mask) in enumerate(renders):
        preview = Path(mesh_path).with_name(f'{Path(mesh_path).stem}.view-{index:02d}.png')
        Image.fromarray(rgb).save(preview)
        previews.append(str(preview))
    from PIL import ImageDraw
    columns = min(3, len(renders))
    rows = (len(renders) + columns - 1) // columns
    sheet = Image.new('RGB', (columns * size, rows * (size + 24)), (245, 245, 245))
    draw = ImageDraw.Draw(sheet)
    for index, ((rgb, _), (azimuth, elevation)) in enumerate(zip(renders, views)):
        x, y = (index % columns) * size, (index // columns) * (size + 24)
        sheet.paste(Image.fromarray(rgb), (x, y))
        draw.text((x + 4, y + size + 4), f'View {index}: yaw {azimuth}, elevation {elevation}', fill='black')
    contact_sheet = Path(mesh_path).with_name(f'{Path(mesh_path).stem}.views.png')
    sheet.save(contact_sheet)

    return {
        "verdict": verdict(silhouette, fill, thresholds, placement),
        "bbox_fill": round(fill, 4),
        "geometry_contract": thresholds.get('geometry_contract', 'volumetric'),
        "watertight": bool(geometry.is_watertight),
        "surface_area": float(geometry.area),
        "silhouette": round(silhouette, 4),
        "silhouette_per_view": [round(v, 4) for v in ious],
        "texture_placement": placement,
        "asset_validation": materials,
        "render_mode": "albedo_only",
        "semantic": None if semantic is None else round(semantic, 4),
        "semantic_control": None if control is None else round(control, 4),
        "thresholds": thresholds,
        "views": [{"azimuth": az, "elevation": el} for az, el in views],
        "mesh_faces": int(len(geometry.faces)),
        "previews": previews,
        "previews_sha256": [hashlib.sha256(Path(path).read_bytes()).hexdigest()
                            for path in previews],
        'contact_sheet': str(contact_sheet),
        "best_view": order,
        "note": "semantic is a raw CLIP cosine (1.0 self, ~0.27 across different "
                "photographs), not a 0..1 quality score",
    }


def main() -> int:
    # Runnable as a plain script inside the engine venv, where the package root is
    # not importable by default.
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)

    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--encoder", type=Path, default=None,
                        help="local CLIP vision tower directory")
    parser.add_argument("--engine-root", type=Path, default=None,
                        help="Hunyuan port root, which provides torch_rasterizer")
    parser.add_argument("--thresholds", type=Path, default=None,
                        help="JSON file overriding the verdict thresholds")
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    thresholds = None
    if args.thresholds:
        thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
    result = evaluate(args.reference, args.mesh, encoder_path=args.encoder,
                      engine_root=args.engine_root, size=args.size,
                      thresholds=thresholds)
    print(json.dumps(result, indent=2))
    if args.output:
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    # A non-zero status tells the caller the measurement is not acceptable, so a
    # pipeline can refuse to deliver. Distinct from the check having failed.
    return 0 if result["verdict"] == "plausible" else 3


if __name__ == "__main__":
    sys.exit(main())
