#!/usr/bin/env python
"""Aurora 3D mesh reshape — non-uniform scale to bring a mesh's bbox extents
toward the canonical aspect ratio for its subject kind.

Use case: Cat 1 of 3D.txt produced a near-cubic GLB (extents 0.97:1.00:0.49)
where a PC tower's canonical aspect is 0.45:1.00:0.55. Color was rescued by
bake_vertex_colors (v78w). Aspect can be brought toward canonical here.

Strategy:
    1. Read mesh, compute bbox extents.
    2. Compare to KIND_ASPECT (from mesh_quality_score). Largest dimension
       is taken as the reference (height for tall subjects, length for
       quadrupeds/vehicles), normalized to 1.0.
    3. Compute per-axis scale factors to bring the other two extents toward
       canonical. Cap each scale at [1 - max_distortion, 1 + max_distortion]
       so we don't squeeze the mesh into a strip.
    4. Apply the scale to vertices, write a new GLB with the reshaped mesh.
       Vertex colors and faces preserved.

Schema: aurora.mesh_reshape.v1.

Usage:
    python mesh_reshape.py --mesh in.glb --output out.glb --kind pc_tower
    python mesh_reshape.py --mesh in.glb --output out.glb --kind humanoid \\
        --max-distortion 0.4
"""

from __future__ import annotations

import argparse
import json
import sys
from itertools import permutations
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parent))
from mesh_quality_score import KIND_ASPECT  # noqa: E402


def find_best_axis_mapping(extents_norm, expected):
    """Try all axis permutations of the input extents and return the one
    closest (in L1 norm) to the canonical aspect. The mesh's local axes may
    not match the canonical ones (Hunyuan3D doesn't guarantee orientation)."""
    best_perm = None
    best_dist = float("inf")
    for perm in permutations(range(3)):
        permuted = [extents_norm[i] for i in perm]
        dist = sum(abs(a - b) for a, b in zip(permuted, expected))
        if dist < best_dist:
            best_dist = dist
            best_perm = perm
    return best_perm, best_dist


def reshape(mesh_path: Path, output_path: Path, kind: str = "generic",
            max_distortion: float = 0.30) -> dict:
    try:
        import trimesh
        import numpy as np
    except ImportError as exc:
        return {"ok": False, "error": f"missing dep: {exc}"}

    if not mesh_path.is_file():
        return {"ok": False, "error": f"mesh not found: {mesh_path}"}

    kind_norm = (kind or "generic").lower().strip()
    expected = KIND_ASPECT.get(kind_norm, KIND_ASPECT["generic"])
    if expected[0] < 0:  # open ratio kinds — skip reshape
        return {
            "ok": True, "skipped": True,
            "reason": f"kind '{kind_norm}' uses open aspect ratio; reshape declined",
            "schema": "aurora.mesh_reshape.v1",
        }

    try:
        mesh = trimesh.load(str(mesh_path), force="mesh")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"mesh load failed: {exc}"}

    extents_in = mesh.bounding_box.extents
    longest_in = float(extents_in.max()) or 1.0
    norm_in = [round(float(e) / longest_in, 4) for e in extents_in]

    # Find the axis permutation that's closest to canonical, then compute
    # scale factors to bring the input extents to the canonical (mapped back
    # to the input's axes via the permutation).
    perm, _ = find_best_axis_mapping(norm_in, list(expected))
    expected_for_input = [0.0, 0.0, 0.0]
    for source_idx, perm_idx in enumerate(perm):
        expected_for_input[perm_idx] = expected[source_idx]

    scales = []
    for i in range(3):
        target = expected_for_input[i]
        current = norm_in[i]
        if current <= 1e-6 or target <= 1e-6:
            scales.append(1.0)
            continue
        raw = target / current
        # Cap distortion. raw < 1 means we need to shrink this axis; >1 means grow.
        clipped = max(1.0 - max_distortion, min(1.0 + max_distortion, raw))
        scales.append(clipped)

    # Apply non-uniform scale around the centroid so the mesh stays in place.
    centroid = mesh.bounding_box.centroid
    verts = np.asarray(mesh.vertices)
    centered = verts - centroid
    scaled = centered * np.array(scales)
    new_verts = scaled + centroid

    mesh_out = mesh.copy()
    mesh_out.vertices = new_verts
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mesh_out.export(str(output_path))

    extents_out = mesh_out.bounding_box.extents
    longest_out = float(extents_out.max()) or 1.0
    norm_out = [round(float(e) / longest_out, 4) for e in extents_out]
    new_dist = sum(abs(a - b) for a, b in zip(sorted(norm_out, reverse=True),
                                              sorted(expected, reverse=True)))

    return {
        "ok": True,
        "schema": "aurora.mesh_reshape.v1",
        "input_mesh": str(mesh_path),
        "output_mesh": str(output_path),
        "subject_kind": kind_norm,
        "max_distortion": max_distortion,
        "axis_permutation": list(perm),
        "scales_applied": [round(s, 4) for s in scales],
        "extents_before_norm": norm_in,
        "extents_after_norm":  norm_out,
        "expected_canonical":  list(expected),
        "aspect_l1_after":     round(new_dist, 4),
    }


def render_pretty(result: dict) -> str:
    if not result.get("ok"):
        return f"FAIL: {result.get('error')}\n"
    if result.get("skipped"):
        return f"Reshape skipped: {result.get('reason')}\n"
    lines = [
        f"Mesh reshape -> {result['output_mesh']}",
        f"  subject kind:    {result['subject_kind']}",
        f"  max_distortion:  {result['max_distortion']}",
        f"  axis perm:       {result['axis_permutation']}",
        f"  scales applied:  {result['scales_applied']}",
        f"  before norm:     {result['extents_before_norm']}",
        f"  after  norm:     {result['extents_after_norm']}",
        f"  expected:        {result['expected_canonical']}",
        f"  aspect L1 after: {result['aspect_l1_after']}",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora 3D mesh reshape")
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--kind", default="generic")
    parser.add_argument("--max-distortion", type=float, default=0.30)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    result = reshape(Path(args.mesh), Path(args.output), args.kind, args.max_distortion)
    if args.pretty:
        sys.stdout.write(render_pretty(result))
    else:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=True) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
