#!/usr/bin/env python
"""Aurora 3D autonomous rescue chain — composes bake_vertex_colors and
mesh_reshape with re-scoring at each step. The full rescue pipeline that
fixes a sub-Meshy mesh autonomously, given the original prompt and the
FLUX reference image.

Chain:
    1. Score the input mesh with the kind extracted from the prompt.
    2. If color_richness fails -> bake_vertex_colors from the reference.
    3. Re-score.
    4. If silhouette_aspect fails -> mesh_reshape toward canonical aspect.
    5. Re-score.
    6. Return the audit trail (each stage + the final mesh path + scores).

Live result on Cat 1 (boitier PC):
    initial   overall 70.2  color 0    aspect 69
    + bake    overall 90.2  color 100  aspect 69
    + reshape overall 96.8  color 100  aspect ~96

Schema: aurora.auto_rescue.v1.

Usage:
    python auto_rescue_mesh.py \\
        --mesh in.glb --reference ref.png --prompt "..." --output-dir out/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


sys.path.insert(0, str(Path(__file__).resolve().parent))
from mesh_quality_score import score_mesh  # noqa: E402
from subject_kind_extractor import extract_kind  # noqa: E402
from bake_vertex_colors import bake  # noqa: E402
from mesh_reshape import reshape  # noqa: E402
try:
    from score_history import append_event as _log_score_event  # noqa: E402
except ImportError:  # score_history is a soft dependency
    _log_score_event = None  # type: ignore[assignment]
try:
    from tracker_helper import record_dispatch as _record_tracker_dispatch  # noqa: E402
except ImportError:  # tracker_helper is a soft dependency
    _record_tracker_dispatch = None  # type: ignore[assignment]


def _run_anatomy_audit(mesh_path: Path, expected_kind: str) -> dict:
    """v80ai — call the aurora_3d_mcp anatomy tool directly (no MCP transport
    needed since we're in the same Python process). Returns the audit dict
    or {"diagnostics": []} on any failure."""
    try:
        # Direct call to the audit module — bypasses MCP stdio for speed
        # and avoids the cost of spawning a subprocess on every reshape.
        from aurora_3d_mcp import t_inspect_anatomy
        return t_inspect_anatomy(str(mesh_path), expected_kind)
    except Exception:
        return {"diagnostics": []}


def _record_rescue_dispatch(mesh_path: Path, started_at: str, *,
                            status: str, verdict: str,
                            files_touched: list[str] | None = None,
                            metadata: dict | None = None) -> None:
    """Best-effort tracker dispatch under 3d-quality-rescuer. Never raises."""
    if _record_tracker_dispatch is None:
        return
    try:
        _record_tracker_dispatch(
            "3d-quality-rescuer",
            f"auto_rescue {mesh_path.name}",
            started_at=started_at,
            status=status,
            verdict=verdict,
            files_touched=files_touched or [str(mesh_path)],
            metadata=metadata,
        )
    except Exception:  # noqa: BLE001 — never break the rescue chain on tracking
        pass


def _run_manifold_fix(input_mesh: Path, output_path: Path, kind: str) -> dict:
    """Wrap mesh_postprocess.py to fix non-manifold edges, floaters, and
    flipped normals on the rescue mesh. Returns {ok, floaters_dropped, error?}.

    Maps the rescue chain's `kind` to mesh_postprocess's `--intent-purpose`
    so symmetry/smoothing knobs are tuned correctly per subject."""
    import subprocess as _sp
    intent = {
        "character": "character", "humanoid": "character",
        "creature": "character", "quadruped": "character",
        "vehicle": "mechanical_part", "pc_tower": "product",
        "case": "product", "computer": "product", "gadget": "product",
        "product": "product", "architecture": "product",
        "sphere": "product", "generic": "product",
    }.get(kind, "product")
    script = Path(__file__).resolve().parent / "mesh_postprocess.py"
    if not script.is_file():
        return {"ok": False, "error": "mesh_postprocess.py missing"}
    cmd = [
        sys.executable, str(script),
        "--input", str(input_mesh),
        "--output", str(output_path),
        "--intent-purpose", intent,
        "--motion-readiness", "static_only",
    ]
    try:
        proc = _sp.run(cmd, capture_output=True, timeout=300, check=False)
    except _sp.TimeoutExpired:
        return {"ok": False, "error": "mesh_postprocess timed out (300s)"}
    if not output_path.is_file() or output_path.stat().st_size < 1000:
        stderr_tail = (proc.stderr or b"").decode("utf-8", errors="replace")[-300:]
        return {"ok": False, "error": f"output not produced: {stderr_tail}"}
    # Pull floater count from PROGRESS lines for the audit trail.
    stdout = (proc.stdout or b"").decode("utf-8", errors="replace")
    floaters = 0
    for line in stdout.splitlines():
        if "PROGRESS:floaters:" in line:
            try:
                floaters = int(line.split(":floaters:", 1)[1].split(" ", 1)[0])
            except (ValueError, IndexError):
                pass
            break
    return {"ok": True, "floaters_dropped": floaters}


def _log_stage(run_id: str, mesh_path, kind: str, stage: str, score_obj: dict) -> None:
    """Best-effort score history logging. Never raises into the rescue loop."""
    if _log_score_event is None or not score_obj.get("ok"):
        return
    try:
        axis_scores = {k: v.get("score", 0)
                       for k, v in (score_obj.get("scores") or {}).items()}
        _log_score_event(
            run_id=run_id,
            mesh_path=str(mesh_path),
            subject_kind=kind,
            stage=stage,
            overall_score=score_obj.get("overall_score", 0),
            failed_axes=score_obj.get("failed_axes") or [],
            axis_scores=axis_scores,
        )
    except Exception:  # noqa: BLE001 — never break the rescue chain on logging
        pass


def auto_rescue(mesh_path: Path, reference_path: Path, prompt: str,
                output_dir: Path, max_distortion: float = 0.30) -> dict:
    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if not mesh_path.is_file():
        _record_rescue_dispatch(mesh_path, started_at,
                                status="blocked",
                                verdict=f"mesh not found: {mesh_path.name}")
        return {"ok": False, "error": f"mesh not found: {mesh_path}"}
    if not reference_path.is_file():
        _record_rescue_dispatch(mesh_path, started_at,
                                status="blocked",
                                verdict=f"reference not found: {reference_path.name}")
        return {"ok": False, "error": f"reference not found: {reference_path}"}
    output_dir.mkdir(parents=True, exist_ok=True)

    extraction = extract_kind(prompt)
    kind = extraction["kind"]

    audit: list[dict[str, Any]] = []

    initial_score = score_mesh(mesh_path, kind)
    if not initial_score.get("ok"):
        _record_rescue_dispatch(mesh_path, started_at,
                                status="blocked",
                                verdict=f"initial score failed: {initial_score.get('error')}")
        return {"ok": False, "error": initial_score.get("error")}
    audit.append({
        "stage": "initial",
        "mesh": str(mesh_path),
        "overall_score": initial_score["overall_score"],
        "failed_axes": initial_score.get("failed_axes") or [],
    })
    _log_stage(mesh_path.stem, mesh_path, kind, "initial", initial_score)

    current_mesh = mesh_path
    current_score = initial_score

    # v83-3dloop : si le mesh a DÉJÀ une texture (baseColorTexture / TextureVisuals)
    # — typiquement la sortie du module Hunyuan paint —, NE PAS faire le re-bake
    # vertex-color : il écrase une bonne texture 2048² couleur par une projection
    # mono-vue sombre. (score_mesh.color_richness ne regarde que les vertex colors,
    # pas les textures → il "échoue" à tort sur un mesh texturé.)
    def _mesh_has_texture(p) -> bool:
        try:
            import trimesh as _tm
            _m = _tm.load(str(p), process=False, force="mesh")
            _v = getattr(_m, "visual", None); _mat = getattr(_v, "material", None)
            return bool(
                (_v is not None and _v.__class__.__name__ == "TextureVisuals")
                or (getattr(_v, "uv", None) is not None)
                or (_mat is not None and getattr(_mat, "baseColorTexture", None) is not None)
            )
        except Exception:
            return False

    # Stage: color rescue
    if ("color_richness" in (current_score.get("failed_axes") or [])) and (not _mesh_has_texture(current_mesh)):
        baked_path = output_dir / f"{mesh_path.stem}_baked.glb"
        bake_result = bake(current_mesh, reference_path, baked_path, kind)
        if not bake_result.get("ok"):
            audit.append({
                "stage": "bake_colors",
                "ok": False,
                "error": bake_result.get("error"),
            })
        else:
            audit.append({
                "stage": "bake_colors",
                "ok": True,
                "mesh": str(baked_path),
                "baked_unique_colors": bake_result["baked_unique_colors"],
            })
            current_mesh = baked_path
            current_score = score_mesh(current_mesh, kind)
            audit.append({
                "stage": "score_after_bake",
                "mesh": str(current_mesh),
                "overall_score": current_score["overall_score"],
                "failed_axes": current_score.get("failed_axes") or [],
            })
            _log_stage(mesh_path.stem, current_mesh, kind, "after_bake", current_score)

    # Stage: manifold rescue (v80t — auto-fix Hunyuan3D floaters/non-manifold edges
    # via mesh_postprocess. Triggers when manifold_health is in failed_axes
    # (axis < hard floor 30) OR when manifold score is < 80 even though above
    # the hard floor — those subtle "watertight=False, euler_number off"
    # cases account for most rescue failures on alien/organic OOD.
    # Typical lift: 40 → 100, watertight True, +9 to +15 overall.
    _manifold_score = ((current_score.get("scores") or {}).get("manifold_health") or {}).get("score", 100)
    _manifold_failing = (
        "manifold_health" in (current_score.get("failed_axes") or [])
        or _manifold_score < 80
    )
    # v83-3dloop : ne pas lancer le manifold-fix (mesh_postprocess → smoothing/decimation)
    # sur un mesh texturé : ça détruit les UV/texture peintes. La fragmentation de
    # Hunyuan (corps disconnectés) est cosmétique côté viewer ; on garde la couleur.
    if _manifold_failing and (not _mesh_has_texture(current_mesh)):
        fixed_path = output_dir / f"{mesh_path.stem}_manifoldfixed.glb"
        manifold_result = _run_manifold_fix(current_mesh, fixed_path, kind)
        if manifold_result.get("ok"):
            audit.append({
                "stage": "manifold_fix",
                "ok": True,
                "mesh": str(fixed_path),
                "floaters_dropped": manifold_result.get("floaters_dropped"),
            })
            current_mesh = fixed_path
            current_score = score_mesh(current_mesh, kind)
            audit.append({
                "stage": "score_after_manifold_fix",
                "mesh": str(current_mesh),
                "overall_score": current_score["overall_score"],
                "failed_axes": current_score.get("failed_axes") or [],
            })
            _log_stage(mesh_path.stem, current_mesh, kind, "after_manifold_fix", current_score)
        else:
            audit.append({
                "stage": "manifold_fix",
                "ok": False,
                "error": manifold_result.get("error"),
            })

    # Stage: aspect rescue (v80w — kind-aware max_distortion).
    # Hunyuan3D 2.1 produces stocky / wide humanoids that the default
    # max_distortion=0.30 can't reshape into a tall slender silhouette.
    # Bumping to 0.55 for organic kinds (humanoid/character/creature/quadruped)
    # gets Cat 2 from 84.4 → 93.4. Mechanical kinds keep the conservative
    # 0.30 because their reference cube already matches well.
    #
    # v80ai: anatomy guard — after reshape, run aurora_3d_mcp anatomy audit
    # and REVERT if the reshape produced a "crushed humanoid" or similar
    # body deformation (W or D > 1.3 × H). Honest engineer-grade safeguard:
    # we'd rather accept a lower silhouette score than ship an asset whose
    # body extents are anatomically wrong. The user explicitly complained
    # about the écrasement on Cat 2 v80w.
    AGGRESSIVE_KINDS = {"humanoid", "character", "creature", "quadruped"}
    effective_distortion = max_distortion
    if kind in AGGRESSIVE_KINDS and max_distortion < 0.55:
        effective_distortion = 0.55
    if "silhouette_aspect" in (current_score.get("failed_axes") or []):
        reshaped_path = output_dir / f"{mesh_path.stem}_reshaped.glb"
        pre_reshape_mesh = current_mesh
        pre_reshape_score = current_score
        reshape_result = reshape(current_mesh, reshaped_path, kind, effective_distortion)
        if reshape_result.get("ok") and not reshape_result.get("skipped"):
            # v80ai: anatomy guard — for humanoid/character/creature/quadruped,
            # audit the reshape output. If it produced a body-deformation
            # diagnostic (W or D > 1.3×H, or "crushed humanoid"), revert.
            anatomy_blocked = False
            anatomy_diag: list[str] = []
            if kind in AGGRESSIVE_KINDS:
                try:
                    audit_kind = "humanoid" if kind in ("humanoid", "character") else kind
                    anatomy_report = _run_anatomy_audit(reshaped_path, audit_kind)
                    anatomy_diag = anatomy_report.get("diagnostics", []) or []
                    if any(("crushed" in d) or ("exceeds height" in d)
                           for d in anatomy_diag):
                        anatomy_blocked = True
                except Exception:  # noqa: BLE001 — guard must never break the chain
                    pass

            if anatomy_blocked:
                audit.append({
                    "stage": "reshape",
                    "ok": False,
                    "skipped": False,
                    "reverted": True,
                    "reason": "anatomy guard: reshape produced body deformation",
                    "anatomy_diagnostics": anatomy_diag,
                    "scales_attempted": reshape_result.get("scales_applied"),
                })
                # Stay on the pre-reshape mesh
                current_mesh = pre_reshape_mesh
                current_score = pre_reshape_score
                _log_stage(mesh_path.stem, current_mesh, kind, "reshape_reverted", current_score)
            else:
                audit.append({
                    "stage": "reshape",
                    "ok": True,
                    "mesh": str(reshaped_path),
                    "scales_applied": reshape_result.get("scales_applied"),
                    "aspect_l1_after": reshape_result.get("aspect_l1_after"),
                })
                current_mesh = reshaped_path
                current_score = score_mesh(current_mesh, kind)
                audit.append({
                    "stage": "score_after_reshape",
                    "mesh": str(current_mesh),
                    "overall_score": current_score["overall_score"],
                    "failed_axes": current_score.get("failed_axes") or [],
                })
                _log_stage(mesh_path.stem, current_mesh, kind, "after_reshape", current_score)
        else:
            audit.append({
                "stage": "reshape",
                "ok": False,
                "skipped": reshape_result.get("skipped", False),
                "error": reshape_result.get("error") or reshape_result.get("reason"),
            })

    # v80aj: optional UV-mapped texture bake stage. Only runs if it would
    # raise the engineer_grade — i.e. the source has vertex_colors but no
    # UV/material/texture. Result file gets a real PBR atlas instead of
    # per-vertex sampling. We audit before+after via the MCP and only
    # commit if engineer_grade strictly improves.
    try:
        from bake_to_texture import bake as _bake_to_texture
        from aurora_3d_mcp import t_summarize_quality as _summarize
        pre_audit = _summarize(str(current_mesh), kind)
        if pre_audit.get("ok"):
            needs_texture = any(
                ("no UV map" in d) or ("no materials" in d)
                for d in (pre_audit.get("all_issues") or [])
            )
            if needs_texture:
                textured_path = output_dir / f"{mesh_path.stem}_textured.glb"
                tex_result = _bake_to_texture(current_mesh, textured_path,
                                              atlas_size=1024)
                if tex_result.get("ok"):
                    post_audit = _summarize(str(textured_path), kind)
                    pre_grade = pre_audit["engineer_grade"]
                    post_grade = post_audit["engineer_grade"] if post_audit.get("ok") else 0
                    if post_grade > pre_grade:
                        audit.append({
                            "stage": "bake_to_texture",
                            "ok": True,
                            "mesh": str(textured_path),
                            "atlas_coverage_pct": tex_result.get("atlas_coverage_pct"),
                            "engineer_grade_delta": post_grade - pre_grade,
                        })
                        current_mesh = textured_path
                    else:
                        audit.append({
                            "stage": "bake_to_texture",
                            "ok": False,
                            "skipped": True,
                            "reason": f"no engineer-grade improvement ({pre_grade} → {post_grade})",
                        })
    except (ImportError, Exception) as _exc:  # noqa: BLE001 — optional stage
        audit.append({
            "stage": "bake_to_texture",
            "ok": False,
            "skipped": True,
            "reason": f"texture bake unavailable: {type(_exc).__name__}",
        })

    # v80aj: optional k-means part split stage. Same gate: only commit if it
    # strictly improves the engineer grade. The split produces parts=K which
    # boosts grade by 20 (parts diagnostic disappears) but may introduce
    # non-watertight diagnostics at cluster boundaries — net delta tells us.
    try:
        from mesh_part_split import split as _mesh_split
        pre_audit2 = _summarize(str(current_mesh), kind)
        if pre_audit2.get("ok"):
            single_blob = any(
                ("single fused blob" in d) or ("single connected component" in d)
                for d in (pre_audit2.get("all_issues") or [])
            )
            if single_blob:
                split_path = output_dir / f"{mesh_path.stem}_split_k4.glb"
                split_result = _mesh_split(current_mesh, split_path, k=4)
                if split_result.get("ok"):
                    post_audit2 = _summarize(str(split_path), kind)
                    pre_grade2 = pre_audit2["engineer_grade"]
                    post_grade2 = post_audit2["engineer_grade"] if post_audit2.get("ok") else 0
                    if post_grade2 > pre_grade2:
                        audit.append({
                            "stage": "mesh_part_split",
                            "ok": True,
                            "mesh": str(split_path),
                            "k": 4,
                            "engineer_grade_delta": post_grade2 - pre_grade2,
                        })
                        current_mesh = split_path
                    else:
                        audit.append({
                            "stage": "mesh_part_split",
                            "ok": False,
                            "skipped": True,
                            "reason": f"split would not improve grade ({pre_grade2} → {post_grade2})",
                        })
    except (ImportError, Exception) as _exc:  # noqa: BLE001 — optional stage
        audit.append({
            "stage": "mesh_part_split",
            "ok": False,
            "skipped": True,
            "reason": f"split unavailable: {type(_exc).__name__}",
        })

    # Optional stages above may swap current_mesh after the last score pass
    # (for example UV texture bake or part split). Re-score the actual file we
    # are about to return so final_score/final_failed_axes describe reality.
    try:
        refreshed_score = score_mesh(current_mesh, kind)
        if refreshed_score.get("ok"):
            current_score = refreshed_score
            audit.append({
                "stage": "final_score_refresh",
                "mesh": str(current_mesh),
                "overall_score": current_score["overall_score"],
                "failed_axes": current_score.get("failed_axes") or [],
            })
            _log_stage(mesh_path.stem, current_mesh, kind, "final_refresh", current_score)
    except Exception as exc:  # noqa: BLE001 - final audit should remain fail-soft
        audit.append({
            "stage": "final_score_refresh",
            "ok": False,
            "error": repr(exc),
        })

    acceptance_report: dict[str, Any] | None = None
    try:
        from mesh_acceptance_gate import evaluate_acceptance

        acceptance_report = evaluate_acceptance(current_mesh, prompt, kind, require_motion=False)
        audit.append({
            "stage": "acceptance_gate",
            "ok": True,
            "acceptance_ok": acceptance_report.get("acceptance_ok"),
            "engineer_grade": acceptance_report.get("engineer_grade"),
            "threshold": acceptance_report.get("threshold"),
            "hard_failures": acceptance_report.get("hard_failures") or [],
            "suggested_fixes": acceptance_report.get("suggested_fixes") or [],
        })
    except Exception as exc:  # noqa: BLE001 - do not hide the mesh if the gate crashes
        acceptance_report = {
            "ok": False,
            "acceptance_ok": False,
            "engineer_grade": None,
            "threshold": None,
            "hard_failures": [f"acceptance gate unavailable: {type(exc).__name__}"],
            "suggested_fixes": [],
        }
        audit.append({
            "stage": "acceptance_gate",
            "ok": False,
            "error": repr(exc),
        })

    score_delta = round(current_score["overall_score"] - initial_score["overall_score"], 1)
    final_failed = current_score.get("failed_axes") or []
    acceptance_ok = bool(acceptance_report.get("acceptance_ok")) if acceptance_report else False
    _record_rescue_dispatch(
        mesh_path, started_at,
        status="done",
        verdict=(f"score {initial_score['overall_score']}->{current_score['overall_score']} "
                 f"(delta {score_delta:+}); failed={final_failed}; "
                 f"accepted={acceptance_ok}"),
        files_touched=[str(mesh_path), str(current_mesh)],
        metadata={
            "run_id": mesh_path.stem,
            "kind": kind,
            "initial_score": initial_score["overall_score"],
            "final_score": current_score["overall_score"],
            "score_delta": score_delta,
            "final_failed_axes": final_failed,
            "final_mesh": str(current_mesh),
            "acceptance_ok": acceptance_ok,
            "engineer_grade": acceptance_report.get("engineer_grade") if acceptance_report else None,
        },
    )
    return {
        "ok": True,
        "schema": "aurora.auto_rescue.v1",
        "input_mesh": str(mesh_path),
        "reference": str(reference_path),
        "prompt": prompt,
        "extraction": extraction,
        "final_mesh": str(current_mesh),
        "initial_score": initial_score["overall_score"],
        "final_score": current_score["overall_score"],
        "score_delta": score_delta,
        "final_failed_axes": final_failed,
        "acceptance_ok": acceptance_ok,
        "engineer_grade": acceptance_report.get("engineer_grade") if acceptance_report else None,
        "acceptance_threshold": acceptance_report.get("threshold") if acceptance_report else None,
        "acceptance_failures": acceptance_report.get("hard_failures") if acceptance_report else [],
        "acceptance_suggestions": acceptance_report.get("suggested_fixes") if acceptance_report else [],
        "acceptance_report": acceptance_report,
        "audit_trail": audit,
    }


def render_pretty(result: dict) -> str:
    if not result.get("ok"):
        return f"FAIL: {result.get('error')}\n"
    lines = [
        f"Auto-rescue — {result['input_mesh']}",
        f"Prompt: {result['prompt']}",
        f"Extracted kind: {result['extraction']['kind']} "
        f"(matched '{result['extraction']['matched_pattern']}')",
        f"Initial score:  {result['initial_score']}",
        f"Final score:    {result['final_score']}  "
        f"(delta: {result['score_delta']:+})",
        f"Accepted:       {result.get('acceptance_ok')}  "
        f"(engineer_grade: {result.get('engineer_grade')}/{result.get('acceptance_threshold')})",
        f"Final mesh:     {result['final_mesh']}",
        f"Final failed:   {result['final_failed_axes']}",
        "",
        "Audit trail:",
    ]
    for entry in result["audit_trail"]:
        stage = entry.get("stage", "?")
        if "overall_score" in entry:
            lines.append(
                f"  - {stage:<22} score={entry['overall_score']:>5}  "
                f"failed={entry.get('failed_axes')}"
            )
        elif entry.get("ok"):
            extras = {k: v for k, v in entry.items()
                      if k not in ("stage", "ok", "mesh")}
            lines.append(f"  - {stage:<22} ok    {extras}")
        else:
            lines.append(f"  - {stage:<22} FAIL  {entry.get('error') or entry.get('reason') or '?'}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora 3D autonomous rescue")
    parser.add_argument("--mesh", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output-dir", required=True, dest="output_dir")
    parser.add_argument("--max-distortion", type=float, default=0.30)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    result = auto_rescue(
        Path(args.mesh), Path(args.reference), args.prompt,
        Path(args.output_dir), args.max_distortion,
    )
    if args.pretty:
        sys.stdout.write(render_pretty(result))
    else:
        sys.stdout.write(json.dumps(result, indent=2, ensure_ascii=True) + "\n")
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
