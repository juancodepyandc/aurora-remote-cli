#!/usr/bin/env python
"""Aurora 3D mesh quality scorer — autonomous validation step that decides
whether a generated GLB is good enough or whether the pipeline should retry
with a different configuration. Designed for the Meshy-equivalent autonomy
the 3D module aims at.

Reads a GLB via trimesh, scores on five axes (each 0-100), and returns a
JSON verdict including a retry recommendation.

Axes:
    color_richness     — vertex color variance + presence
    geometric_density  — vertex/face count vs expected for the subject kind
    silhouette_aspect  — bbox aspect ratio vs the canonical aspect for kind
    manifold_health    — closed surface, no degenerate faces / holes
    surface_quality    — average face area variance (low = clean topology)

Subject-kind canonical aspects (width:height:depth, normalized):
    character / humanoid : 0.30 : 1.00 : 0.20  (tall, slim)
    quadruped / creature : 0.55 : 0.55 : 1.00  (long body)
    pc_tower / case      : 0.35 : 0.85 : 0.45  (rectangular tower)
    vehicle              : 0.45 : 0.30 : 1.00  (long, low)
    product / gadget     : open ratio (tolerant)
    architecture         : open ratio
    sphere / generic     : open ratio

Usage:
    python mesh_quality_score.py --mesh path.glb --kind pc_tower
    python mesh_quality_score.py --mesh path.glb --kind character --pretty

Stdlib + numpy + trimesh (already installed for the rest of the 3D pipeline).
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path


# Canonical aspect ratios per subject kind. Tuple is (width, height, depth)
# normalized so the largest dimension == 1.0. We compare the input mesh's
# normalized bbox dimensions and score on L1 distance.
KIND_ASPECT: dict[str, tuple[float, float, float]] = {
    "character":       (0.30, 1.00, 0.20),
    "humanoid":        (0.30, 1.00, 0.20),
    "quadruped":       (0.55, 0.55, 1.00),
    "creature":        (0.55, 0.55, 1.00),
    "pc_tower":        (0.45, 1.00, 0.55),
    "case":            (0.45, 1.00, 0.55),
    "computer":        (0.45, 1.00, 0.55),
    "vehicle":         (0.45, 0.30, 1.00),
    "product":         (-1, -1, -1),  # open
    "gadget":          (-1, -1, -1),
    "architecture":    (-1, -1, -1),
    "mechanism":       (-1, -1, -1),
    "mechanical":      (-1, -1, -1),
    "sphere":          (-1, -1, -1),
    "generic":         (-1, -1, -1),
}

# Expected vertex floor per kind. Below this, the mesh is suspiciously simple.
KIND_VERTEX_FLOOR: dict[str, int] = {
    "character":  20000,
    "humanoid":   20000,
    "quadruped":  15000,
    "creature":   15000,
    "pc_tower":   30000,   # PC cases have lots of edges + panels
    "case":       30000,
    "computer":   30000,
    "vehicle":    25000,
    "product":    8000,
    "gadget":     8000,
    "architecture": 15000,
    "mechanism":   7000,
    "mechanical":  7000,
    "sphere":     500,
    "generic":    5000,
}

RETRY_THRESHOLD = 60  # below this overall score, recommend retry

# Per-axis hard floors — if any axis falls below its floor, retry is forced
# regardless of overall score. Color and silhouette are dealbreakers for
# Meshy-equivalent quality: a monochrome PC case or a square humanoid is
# never "good enough", no matter how clean the topology is.
AXIS_HARD_FLOORS: dict[str, int] = {
    "color_richness":    25,
    "silhouette_aspect": 40,
    "manifold_health":   30,
}


def _read_gltf_json(mesh_path: Path) -> dict:
    """Read glTF JSON from GLB/GLTF for material and texture scoring."""
    try:
        if mesh_path.suffix.lower() == ".gltf":
            return json.loads(mesh_path.read_text(encoding="utf-8"))
        with open(mesh_path, "rb") as f:
            if f.read(4) != b"glTF":
                return {}
            f.read(8)  # version + total length
            json_len, json_type = struct.unpack("<II", f.read(8))
            if json_type != 0x4E4F534A:
                return {}
            return json.loads(f.read(json_len).rstrip(b"\x00 "))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _score_material_palette(mesh_path: Path) -> tuple[int, dict]:
    gltf = _read_gltf_json(mesh_path)
    materials = gltf.get("materials") or []
    textures = gltf.get("textures") or []
    images = gltf.get("images") or []
    colors: list[tuple[float, float, float]] = []
    textured_materials = 0
    for mat in materials:
        pbr = mat.get("pbrMetallicRoughness") or {}
        if pbr.get("baseColorTexture") is not None:
            textured_materials += 1
        factor = pbr.get("baseColorFactor")
        if isinstance(factor, list) and len(factor) >= 3:
            try:
                colors.append((float(factor[0]), float(factor[1]), float(factor[2])))
            except (TypeError, ValueError):
                pass

    detail = {
        "has_materials": bool(materials),
        "material_count": len(materials),
        "base_color_count": len(colors),
        "textured_materials": textured_materials,
        "texture_count": len(textures),
        "image_count": len(images),
        "material_unique_colors": 0,
        "material_palette_spread": 0.0,
        "low_contrast_on_light_ratio": 0.0,
    }
    if not materials:
        return 0, detail

    score = 20
    has_texture_signal = textured_materials > 0 or len(textures) > 0 or len(images) > 0
    if colors:
        rounded = {
            tuple(max(0, min(255, int(round(channel * 255)))) for channel in color)
            for color in colors
        }
        channels = list(zip(*colors))
        spread = sum((max(channel) - min(channel)) for channel in channels) / 3.0
        low_contrast = 0
        for r, g, b in colors:
            brightness = (r + g + b) / 3.0
            chroma = max(r, g, b) - min(r, g, b)
            if brightness > 0.82 and chroma < 0.10:
                low_contrast += 1
        detail["material_unique_colors"] = len(rounded)
        detail["material_palette_spread"] = round(float(spread), 4)
        detail["low_contrast_on_light_ratio"] = round(float(low_contrast) / float(len(colors)), 3)
        score += min(35, len(rounded) * 8)
        score += min(25, int(spread * 100))

    if textured_materials:
        score += min(25, 15 + textured_materials * 5)
    if len(textures) > 0 or len(images) > 0:
        score += 10

    if (
        not has_texture_signal
        and detail["base_color_count"] <= 1
        and detail["material_palette_spread"] < 0.08
    ):
        score = min(score, 35)
    if (
        not has_texture_signal
        and detail["low_contrast_on_light_ratio"] >= 0.8
    ):
        score = min(score, 40)
    return max(0, min(100, score)), detail


def score_color_richness(mesh, mesh_path: Path | None = None) -> tuple[int, dict]:
    """Color fidelity from vertex colors and GLB materials/textures."""
    material_score, material_detail = _score_material_palette(mesh_path) if mesh_path is not None else (0, {
        "has_materials": False,
        "material_count": 0,
        "base_color_count": 0,
        "textured_materials": 0,
        "texture_count": 0,
        "image_count": 0,
        "material_unique_colors": 0,
        "material_palette_spread": 0.0,
        "low_contrast_on_light_ratio": 0.0,
    })
    detail = {
        "has_vertex_colors": False,
        "unique_colors": 0,
        "variance": 0.0,
        "vertex_color_score": 0,
        "material_color_score": material_score,
        **material_detail,
    }
    try:
        import numpy as np
    except ImportError:
        return material_score, detail

    visual = getattr(mesh, "visual", None)
    if visual is None:
        return material_score, detail
    vc = getattr(visual, "vertex_colors", None)
    if vc is None or len(vc) == 0:
        return material_score, detail

    detail["has_vertex_colors"] = True
    arr = np.asarray(vc)[:, :3].astype(float) / 255.0
    # Round to 1/256 buckets so micro-variations don't inflate uniqueness.
    rounded = np.round(arr * 255).astype(int)
    unique = len(np.unique(rounded.view([("", rounded.dtype)] * rounded.shape[1])))
    detail["unique_colors"] = int(unique)
    var = float(arr.var(axis=0).sum())
    detail["variance"] = round(var, 4)

    # Score: 0 unique → 0, ≥256 unique → 60+. Variance contributes up to 40.
    vertex_score = min(60, int(unique / 50))
    vertex_score += min(40, int(var * 1000))
    detail["vertex_color_score"] = min(100, vertex_score)
    return max(material_score, detail["vertex_color_score"]), detail


def score_geometric_density(mesh, kind: str) -> tuple[int, dict]:
    detail = {"vertex_count": int(len(mesh.vertices)), "face_count": int(len(mesh.faces))}
    floor = KIND_VERTEX_FLOOR.get(kind, KIND_VERTEX_FLOOR["generic"])
    detail["vertex_floor"] = floor
    if detail["vertex_count"] >= floor:
        score = 100
    else:
        score = max(0, int(100 * detail["vertex_count"] / floor))
    detail["passes_floor"] = score >= 80
    return score, detail


def score_silhouette_aspect(mesh, kind: str) -> tuple[int, dict]:
    extents = mesh.bounding_box.extents.tolist()
    longest = max(extents) or 1.0
    norm = [round(e / longest, 3) for e in extents]
    detail = {"extents_m": [round(e, 4) for e in extents], "normalized": norm}

    expected = KIND_ASPECT.get(kind, (-1, -1, -1))
    if expected[0] < 0:
        detail["expected"] = "open"
        return 100, detail

    detail["expected"] = list(expected)
    # L1 distance, but try all 6 axis permutations because the export axis may
    # differ from canonical (rare, but happens).
    from itertools import permutations
    best_dist = min(
        sum(abs(a - b) for a, b in zip(perm, expected))
        for perm in permutations(norm)
    )
    detail["aspect_l1_distance"] = round(best_dist, 3)
    # 0.0 → 100, 1.0 → 0
    score = max(0, int(100 * (1.0 - best_dist / 1.5)))
    return min(100, score), detail


def score_manifold_health(mesh) -> tuple[int, dict]:
    detail = {"is_watertight": False, "euler_number": None, "broken_faces": 0}
    try:
        detail["is_watertight"] = bool(mesh.is_watertight)
        detail["euler_number"] = int(mesh.euler_number)
        broken = getattr(mesh, "broken_faces", None)
        if broken is not None:
            detail["broken_faces"] = int(len(broken))
    except Exception as exc:  # noqa: BLE001 — trimesh raises various
        detail["error"] = str(exc)[:100]
        return 30, detail

    score = 0
    if detail["is_watertight"]:
        score += 60
    if detail["broken_faces"] == 0:
        score += 40
    elif detail["broken_faces"] < 100:
        score += 20
    return min(100, score), detail


def score_surface_quality(mesh) -> tuple[int, dict]:
    detail = {}
    try:
        import numpy as np
        face_areas = np.asarray(mesh.area_faces)
        face_areas = face_areas[face_areas > 0]
        if len(face_areas) == 0:
            return 0, detail
        mean = float(face_areas.mean())
        std = float(face_areas.std())
        detail["mean_face_area"] = round(mean, 8)
        detail["std_face_area"] = round(std, 8)
        # Low cv (std / mean) = clean topology. cv > 5 means very irregular.
        # Multi-part assets (characters with tiny eyes/hair plus large clothes,
        # mechanisms with pins plus plates) legitimately mix face sizes, so we
        # also compute a 5-95% trimmed score. Broken faces/manifold are checked
        # elsewhere; this axis should catch rough topology, not punish details.
        if mean > 0:
            cv = std / mean
            detail["coefficient_of_variation"] = round(cv, 3)
            naive_score = max(0, int(100 - cv * 10))
            q05, q50, q95 = np.percentile(face_areas, [5, 50, 95])
            trimmed = face_areas[(face_areas >= q05) & (face_areas <= q95)]
            if len(trimmed) > 0 and float(trimmed.mean()) > 0:
                robust_cv = float(trimmed.std()) / float(trimmed.mean())
                ratio_95_50 = float(q95 / q50) if q50 > 0 else 999.0
                detail["trimmed_coefficient_of_variation"] = round(robust_cv, 3)
                detail["face_area_q05"] = round(float(q05), 8)
                detail["face_area_q50"] = round(float(q50), 8)
                detail["face_area_q95"] = round(float(q95), 8)
                detail["face_area_q95_q50_ratio"] = round(ratio_95_50, 3)
                robust_score = int(100 - robust_cv * 20 - max(0.0, ratio_95_50 - 12.0) * 1.5)
                score = max(naive_score, max(0, robust_score))
            else:
                score = naive_score
        else:
            score = 0
    except Exception as exc:  # noqa: BLE001
        detail["error"] = str(exc)[:100]
        return 50, detail
    return min(100, score), detail


def score_mesh(path: str | Path, kind: str = "generic") -> dict:
    try:
        import trimesh
    except ImportError as exc:
        return {"ok": False, "error": f"trimesh not available: {exc}"}
    p = Path(path)
    if not p.is_file():
        return {"ok": False, "error": f"mesh not found: {path}"}

    try:
        mesh = trimesh.load(str(p), force="mesh")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"trimesh load failed: {exc}"}

    if not hasattr(mesh, "vertices"):
        return {"ok": False, "error": "loaded object is not a mesh"}

    kind_norm = (kind or "generic").lower().strip()
    if kind_norm not in KIND_ASPECT:
        kind_norm = "generic"

    color_score, color_detail = score_color_richness(mesh, p)
    density_score, density_detail = score_geometric_density(mesh, kind_norm)
    aspect_score, aspect_detail = score_silhouette_aspect(mesh, kind_norm)
    manifold_score, manifold_detail = score_manifold_health(mesh)
    surface_score, surface_detail = score_surface_quality(mesh)

    overall = round(
        0.20 * color_score
        + 0.20 * density_score
        + 0.30 * aspect_score
        + 0.15 * manifold_score
        + 0.15 * surface_score,
        1,
    )

    # Per-axis hard floors — any failure forces retry regardless of overall.
    axis_scores = {
        "color_richness": color_score,
        "silhouette_aspect": aspect_score,
        "manifold_health": manifold_score,
    }
    failed_axes = [
        axis for axis, floor in AXIS_HARD_FLOORS.items()
        if axis_scores.get(axis, 100) < floor
    ]

    retry = overall < RETRY_THRESHOLD or bool(failed_axes)
    retry_reason = []
    if overall < RETRY_THRESHOLD:
        retry_reason.append(f"overall {overall} < {RETRY_THRESHOLD}")
    for axis in failed_axes:
        retry_reason.append(
            f"{axis} {axis_scores[axis]} < hard floor {AXIS_HARD_FLOORS[axis]}"
        )

    return {
        "ok": True,
        "schema": "aurora.mesh_quality.v1",
        "mesh_path": str(p),
        "subject_kind": kind_norm,
        "overall_score": overall,
        "retry_recommended": retry,
        "retry_threshold": RETRY_THRESHOLD,
        "retry_reasons": retry_reason,
        "failed_axes": failed_axes,
        "scores": {
            "color_richness": {"score": color_score, **color_detail},
            "geometric_density": {"score": density_score, **density_detail},
            "silhouette_aspect": {"score": aspect_score, **aspect_detail},
            "manifold_health": {"score": manifold_score, **manifold_detail},
            "surface_quality": {"score": surface_score, **surface_detail},
        },
    }


def render_pretty(result: dict) -> str:
    if not result.get("ok"):
        return f"FAIL: {result.get('error')}\n"
    lines = [
        f"Mesh quality — {result['mesh_path']}",
        f"Subject kind: {result['subject_kind']}",
        f"Overall score: {result['overall_score']} / 100  "
        f"({'RETRY' if result['retry_recommended'] else 'OK'})",
    ]
    reasons = result.get("retry_reasons") or []
    if reasons:
        lines.append("Retry reasons:")
        for r in reasons:
            lines.append(f"  - {r}")
    lines.append("")
    for axis, payload in result["scores"].items():
        lines.append(f"  {axis:<20} {payload['score']:>3} / 100")
        for k, v in payload.items():
            if k == "score":
                continue
            lines.append(f"    {k}: {v}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora 3D mesh quality scorer")
    parser.add_argument("--mesh", required=True, help="Path to GLB/GLTF file")
    parser.add_argument("--kind", default="generic",
                        help=f"Subject kind: {sorted(KIND_ASPECT)}")
    parser.add_argument("--pretty", action="store_true",
                        help="Human-readable output instead of JSON")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    result = score_mesh(args.mesh, args.kind)
    if args.pretty:
        sys.stdout.write(render_pretty(result))
    else:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
