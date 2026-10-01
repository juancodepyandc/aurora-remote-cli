#!/usr/bin/env python
"""Aurora 3D color baker — restore vertex colors on a GLB by projecting the
FLUX reference image onto the mesh from the front view.

When Hunyuan3D collapses colors to monochrome (Cat 1 of 3D.txt), this is
the rescue: load the original FLUX reference PNG, project each vertex onto
the image plane, sample, and write a new GLB with proper colored vertices.
The mesh_quality_score color_richness axis goes from 0 to ~50+ in one shot.

Strategy:
    1. Load mesh, compute bounding box.
    2. Compute camera-aligned axes from the largest two extents (default
       assumes Y is up, X is left-right). Project vertices onto XY plane,
       normalize to [0, 1] × [0, 1].
    3. For each vertex, sample the reference image at the projected UV.
       Vertices on the "back" (Z < center) get a darkened version (60%
       brightness) so the front face stands out — better than uniform.
    4. Optionally re-orient: if the mesh's longest axis isn't Y (e.g. a
       quadruped), pick the canonical projection axes per subject_kind.
    5. Write a new GLB with the new vertex_colors. Original mesh untouched.

Usage:
    python bake_vertex_colors.py \\
        --mesh in.glb --reference ref.png --output out.glb
    python bake_vertex_colors.py \\
        --mesh in.glb --reference ref.png --output out.glb --kind pc_tower

Schema: aurora.color_bake.v1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


# Canonical projection: which 3D axes map to image (U, V) for a subject kind.
# (u_axis, v_axis, depth_axis), where 0=X, 1=Y, 2=Z and v_axis is "up".
KIND_PROJECTION: dict[str, tuple[int, int, int]] = {
    "character":  (0, 1, 2),  # X-right, Y-up, Z-depth (front view)
    "humanoid":   (0, 1, 2),
    "quadruped":  (2, 1, 0),  # Z-right (long body), Y-up, X-side
    "creature":   (0, 1, 2),
    "pc_tower":   (0, 1, 2),  # rectangular tower from front
    "case":       (0, 1, 2),
    "computer":   (0, 1, 2),
    "vehicle":    (2, 1, 0),  # long cars project along Z
    "product":    (0, 1, 2),
    "gadget":     (0, 1, 2),
    "architecture": (0, 1, 2),
    "sphere":     (0, 1, 2),
    "generic":    (0, 1, 2),
}


def _zone_from_normal(normals_z: "np.ndarray", thresh_front: float = 0.3,
                      thresh_back: float = -0.3) -> "np.ndarray":
    """Classify each vertex into 0=front (normal_z > thresh_front),
    1=side (between thresholds), 2=back (normal_z < thresh_back).
    Returns int array of length N."""
    import numpy as np
    zones = np.full(len(normals_z), 1, dtype=np.int8)  # default side
    zones[normals_z > thresh_front] = 0  # front
    zones[normals_z < thresh_back] = 2   # back
    return zones


def bake(mesh_path: Path, reference_path: Path, output_path: Path,
         kind: str = "generic", back_dim: float = 0.6,
         multi_zone: bool = False) -> dict:
    try:
        import trimesh
        import numpy as np
        from PIL import Image
    except ImportError as exc:
        return {"ok": False, "error": f"missing dep: {exc}"}

    if not mesh_path.is_file():
        return {"ok": False, "error": f"mesh not found: {mesh_path}"}
    if not reference_path.is_file():
        return {"ok": False, "error": f"reference not found: {reference_path}"}

    try:
        mesh = trimesh.load(str(mesh_path), force="mesh")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"mesh load failed: {exc}"}
    if not hasattr(mesh, "vertices") or len(mesh.vertices) == 0:
        return {"ok": False, "error": "mesh has no vertices"}

    try:
        with Image.open(reference_path) as im:
            ref = np.asarray(im.convert("RGB"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"reference load failed: {exc}"}

    h, w = ref.shape[:2]

    kind_norm = (kind or "generic").lower().strip()
    u_axis, v_axis, d_axis = KIND_PROJECTION.get(kind_norm, KIND_PROJECTION["generic"])

    verts = np.asarray(mesh.vertices)
    bbox_min = verts.min(axis=0)
    bbox_max = verts.max(axis=0)
    extents = bbox_max - bbox_min
    # Avoid div-by-zero on degenerate axes.
    extents = np.where(extents > 1e-9, extents, 1.0)

    # Project to UV in [0,1]
    u = (verts[:, u_axis] - bbox_min[u_axis]) / extents[u_axis]
    v = (verts[:, v_axis] - bbox_min[v_axis]) / extents[v_axis]
    # Image V axis is top-down; flip so Y-up world maps to image up.
    v_flipped = 1.0 - v

    # Pixel coords (clamp inside)
    px = np.clip((u * (w - 1)).astype(int), 0, w - 1)
    py = np.clip((v_flipped * (h - 1)).astype(int), 0, h - 1)
    sampled = ref[py, px]  # (N, 3)

    if multi_zone:
        # Use vertex normals to classify into front/side/back zones, then
        # apply different transformations per zone:
        #   front -> sampled colors as-is (full brightness)
        #   side  -> sampled colors at 80% brightness, slight horizontal flip
        #   back  -> sampled mirrored U + at back_dim brightness
        try:
            normals = np.asarray(mesh.vertex_normals)
        except Exception:  # noqa: BLE001 — trimesh sometimes fails on degenerate meshes
            normals = None
        if normals is None or len(normals) != len(verts):
            # Fallback to depth-based classification when normals unavailable.
            depth_n = (verts[:, d_axis] - bbox_min[d_axis]) / extents[d_axis]
            normal_z_proxy = (0.5 - depth_n) * 2.0  # +1 at front, -1 at back
            zones = _zone_from_normal(normal_z_proxy)
        else:
            zones = _zone_from_normal(normals[:, d_axis])

        # Resample for back zone with mirrored U so left/right swap (typical
        # back-of-PC has different layout than front).
        px_back = np.clip(((1.0 - u) * (w - 1)).astype(int), 0, w - 1)
        sampled_back = ref[py, px_back]

        rgb = sampled.astype(float).copy()
        # side zone: 80% brightness, no resample
        side_mask = zones == 1
        rgb[side_mask] *= 0.8
        # back zone: replace with mirrored sample at back_dim brightness
        back_mask = zones == 2
        rgb[back_mask] = sampled_back[back_mask].astype(float) * back_dim
        rgb = rgb.clip(0, 255).astype(np.uint8)
        zone_breakdown = {
            "front": int((zones == 0).sum()),
            "side":  int(side_mask.sum()),
            "back":  int(back_mask.sum()),
        }
    else:
        # Single-view planar bake (default): dim back-side vertices for
        # silhouette contrast.
        z_norm = (verts[:, d_axis] - bbox_min[d_axis]) / extents[d_axis]
        dim_factor = np.where(
            z_norm > 0.5, back_dim + (1.0 - back_dim) * (1.0 - z_norm) * 2.0, 1.0,
        )
        dim_factor = np.clip(dim_factor, back_dim, 1.0)
        rgb = (sampled.astype(float) * dim_factor[:, np.newaxis]).clip(0, 255).astype(np.uint8)
        zone_breakdown = None

    # Build (N, 4) RGBA, fully opaque.
    rgba = np.concatenate([rgb, np.full((len(rgb), 1), 255, dtype=np.uint8)], axis=1)

    # Write a copy of the mesh with new vertex colors. Don't mutate the input.
    mesh_out = mesh.copy()
    mesh_out.visual = trimesh.visual.color.ColorVisuals(mesh_out, vertex_colors=rgba)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mesh_out.export(str(output_path))

    # Quick stats for the verdict.
    unique_colors = int(len(np.unique(rgb.view(np.dtype((np.void, rgb.dtype.itemsize * 3))))))
    variance = float(rgb.astype(float).var(axis=0).sum())

    return {
        "ok": True,
        "schema": "aurora.color_bake.v1",
        "input_mesh": str(mesh_path),
        "reference": str(reference_path),
        "output_mesh": str(output_path),
        "subject_kind": kind_norm,
        "projection_axes": {"u": u_axis, "v": v_axis, "depth": d_axis},
        "vertex_count": int(len(verts)),
        "baked_unique_colors": unique_colors,
        "baked_variance": round(variance, 2),
        "multi_zone": multi_zone,
        "zone_breakdown": zone_breakdown,
    }


def render_pretty(result: dict) -> str:
    if not result.get("ok"):
        return f"FAIL: {result.get('error')}\n"
    lines = [
        f"Color bake — wrote {result['output_mesh']}",
        f"  input mesh:    {result['input_mesh']}",
        f"  reference:     {result['reference']}",
        f"  subject kind:  {result['subject_kind']}",
        f"  vertices:      {result['vertex_count']}",
        f"  unique colors after bake: {result['baked_unique_colors']}",
        f"  variance:      {result['baked_variance']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora 3D color baker")
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kind", default="generic")
    parser.add_argument("--back-dim", type=float, default=0.6,
                        help="Dim factor for back-facing vertices (default 0.6)")
    parser.add_argument("--multi-zone", action="store_true",
                        help="Use vertex normals to classify front/side/back "
                             "and resample back with mirrored U")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    result = bake(
        Path(args.mesh), Path(args.reference), Path(args.output),
        args.kind, args.back_dim, args.multi_zone,
    )
    if args.pretty:
        sys.stdout.write(render_pretty(result))
    else:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=True) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
