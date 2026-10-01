#!/usr/bin/env python
"""Aurora 3D score history — append-only JSONL log of every mesh quality
score event so the dashboard / metrics endpoint can show progression
per run_id over time.

Each entry is one line of JSON:

   {"ts": "2026-04-30T12:00:00Z",
    "run_id": "cat5_jellopus",
    "mesh_path": "application/output/3d/cat5_jellopus_mesh_baked.glb",
    "subject_kind": "creature",
    "stage": "after_bake",     // or "initial" / "after_reshape" / "ad_hoc"
    "overall_score": 87.7,
    "failed_axes": [],
    "axis_scores": {"color_richness": 100, ...}}

Schema: `aurora.score_event.v1`. The file lives at
`application/output/3d/score_history.jsonl` and rotates after 5000 lines
to `score_history-<UTC>.jsonl` to keep the live file readable.

Usage:
   from score_history import append_event, load_events, top_runs
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOG = REPO_ROOT / "application" / "output" / "3d" / "score_history.jsonl"
ROTATE_AT_LINES = 5000


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def maybe_rotate(log: Path) -> None:
    if not log.is_file():
        return
    try:
        with open(log, "rb") as f:
            line_count = sum(1 for _ in f)
    except OSError:
        return
    if line_count < ROTATE_AT_LINES:
        return
    rotated = log.with_name(f"score_history-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.jsonl")
    try:
        os.replace(log, rotated)
    except OSError:
        pass


def append_event(run_id: str, mesh_path: str, subject_kind: str,
                 stage: str, overall_score: float, failed_axes: list[str],
                 axis_scores: dict[str, int] | None = None,
                 log_path: Path = DEFAULT_LOG) -> dict:
    """Append a single score event. Idempotent in spirit (we never reject)."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    maybe_rotate(log_path)
    entry = {
        "schema": "aurora.score_event.v1",
        "ts": now_iso(),
        "run_id": run_id,
        "mesh_path": str(mesh_path),
        "subject_kind": subject_kind,
        "stage": stage,
        "overall_score": float(overall_score),
        "failed_axes": list(failed_axes),
        "axis_scores": axis_scores or {},
    }
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return entry


def load_events(log_path: Path = DEFAULT_LOG, *,
                run_id: str | None = None,
                limit: int | None = None) -> list[dict]:
    """Return events from the log, newest-first. Optionally filter by run_id."""
    if not log_path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue
                if run_id and entry.get("run_id") != run_id:
                    continue
                out.append(entry)
    except OSError:
        return []
    out.reverse()
    if limit is not None:
        out = out[:limit]
    return out


def compute_trend(log_path: Path = DEFAULT_LOG) -> dict:
    """System-wide rescue effectiveness trend: total events, distinct runs,
    delta stats, which axis the rescue chain lifted most often, and a
    promote rate (% of runs whose final score beat initial).

    Schema: aurora.score_trend.v1. Empty log returns zeros (never raises).
    """
    events = load_events(log_path)
    if not events:
        return {
            "schema": "aurora.score_trend.v1",
            "total_events": 0,
            "total_runs": 0,
            "promoted_runs": 0,
            "promote_rate": 0.0,
            "mean_delta": 0.0,
            "best_delta": 0.0,
            "worst_delta": 0.0,
            "axis_lifts": {},
            "kind_breakdown": {},
            "last_ts": None,
        }

    by_run: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_run[e.get("run_id", "?")].append(e)

    deltas: list[float] = []
    promoted = 0
    axis_lifts: dict[str, int] = defaultdict(int)
    kind_counts: dict[str, int] = defaultdict(int)

    for run_id, run_events in by_run.items():
        chrono = list(reversed(run_events))
        initial = next((e for e in chrono if e.get("stage") == "initial"), chrono[0])
        final = chrono[-1]
        d = float(final.get("overall_score", 0) or 0) - float(initial.get("overall_score", 0) or 0)
        deltas.append(d)
        if d > 0:
            promoted += 1
        kind_counts[final.get("subject_kind") or "unknown"] += 1

        # Axis lifts: every axis that was in initial.failed_axes but absent
        # from final.failed_axes counts as one rescued axis.
        initial_failed = set(initial.get("failed_axes") or [])
        final_failed = set(final.get("failed_axes") or [])
        for axis in initial_failed - final_failed:
            axis_lifts[axis] += 1

    total_runs = len(by_run)
    return {
        "schema": "aurora.score_trend.v1",
        "total_events": len(events),
        "total_runs": total_runs,
        "promoted_runs": promoted,
        "promote_rate": round(promoted / max(1, total_runs), 3),
        "mean_delta": round(sum(deltas) / max(1, len(deltas)), 1),
        "best_delta": round(max(deltas), 1) if deltas else 0.0,
        "worst_delta": round(min(deltas), 1) if deltas else 0.0,
        "axis_lifts": dict(sorted(axis_lifts.items(), key=lambda kv: -kv[1])),
        "kind_breakdown": dict(sorted(kind_counts.items(), key=lambda kv: -kv[1])),
        "last_ts": events[0].get("ts"),
    }


def top_runs(log_path: Path = DEFAULT_LOG, n: int = 10) -> list[dict]:
    """For each run_id, return the latest event + score progression
    (initial → final). Sorted by best final overall_score desc."""
    by_run: dict[str, list[dict]] = defaultdict(list)
    for e in load_events(log_path):
        by_run[e.get("run_id", "?")].append(e)

    summaries: list[dict] = []
    for run_id, events in by_run.items():
        # Events are newest-first; reverse to get chronological.
        chrono = list(reversed(events))
        initial = next((e for e in chrono if e.get("stage") == "initial"), chrono[0])
        final = chrono[-1]
        summaries.append({
            "run_id": run_id,
            "subject_kind": final.get("subject_kind"),
            "initial_score": initial.get("overall_score"),
            "final_score": final.get("overall_score"),
            "score_delta": round(
                (final.get("overall_score", 0) or 0)
                - (initial.get("overall_score", 0) or 0), 1),
            "stages": [e.get("stage") for e in chrono],
            "events_count": len(events),
            "last_ts": final.get("ts"),
        })
    summaries.sort(key=lambda s: -(s.get("final_score") or 0))
    return summaries[:n]


def main() -> int:
    parser = argparse.ArgumentParser(description="Aurora 3D score history")
    parser.add_argument("--log", default=str(DEFAULT_LOG))
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub_top = sub.add_parser("top", help="show top N runs by final score")
    sub_top.add_argument("-n", type=int, default=10)
    sub_top.add_argument("--pretty", action="store_true")
    sub_runs = sub.add_parser("run", help="show events for a single run_id")
    sub_runs.add_argument("--run-id", required=True, dest="run_id")
    sub_runs.add_argument("--pretty", action="store_true")
    sub_app = sub.add_parser("append", help="manually append a score event")
    sub_app.add_argument("--run-id", required=True, dest="run_id")
    sub_app.add_argument("--mesh", required=True)
    sub_app.add_argument("--kind", default="generic")
    sub_app.add_argument("--stage", default="ad_hoc")
    sub_app.add_argument("--score", type=float, required=True)
    sub_app.add_argument("--failed", default="", help="comma-sep failed axes")
    sub_trend = sub.add_parser("trend", help="aggregate rescue effectiveness")
    sub_trend.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    log_path = Path(args.log)

    if args.cmd == "top":
        rows = top_runs(log_path, args.n)
        if args.pretty:
            sys.stdout.write(f"Top {args.n} runs by final score:\n")
            for r in rows:
                sys.stdout.write(
                    f"  {r['run_id']:<28} {r['subject_kind']:<12} "
                    f"{r['initial_score']!s:>5} -> {r['final_score']!s:>5}  "
                    f"(delta {r['score_delta']:+}, {r['events_count']} events)\n"
                )
        else:
            sys.stdout.write(json.dumps({"top": rows}, indent=2, ensure_ascii=True) + "\n")
        return 0

    if args.cmd == "run":
        evts = load_events(log_path, run_id=args.run_id)
        if args.pretty:
            sys.stdout.write(f"Events for run_id={args.run_id} ({len(evts)}):\n")
            for e in reversed(evts):
                sys.stdout.write(
                    f"  {e['ts']}  {e['stage']:<18} {e['overall_score']:>5}  "
                    f"failed={e['failed_axes']}\n"
                )
        else:
            sys.stdout.write(json.dumps({"events": evts}, indent=2, ensure_ascii=True) + "\n")
        return 0

    if args.cmd == "append":
        failed = [a.strip() for a in (args.failed or "").split(",") if a.strip()]
        entry = append_event(
            args.run_id, args.mesh, args.kind, args.stage,
            args.score, failed, log_path=log_path,
        )
        sys.stdout.write(json.dumps(entry, indent=2, ensure_ascii=True) + "\n")
        return 0

    if args.cmd == "trend":
        trend = compute_trend(log_path)
        if args.pretty:
            sys.stdout.write(
                f"Rescue trend: {trend['promoted_runs']}/{trend['total_runs']} runs promoted "
                f"({int(trend['promote_rate'] * 100)}%), "
                f"mean delta {trend['mean_delta']:+}, "
                f"best {trend['best_delta']:+}, worst {trend['worst_delta']:+}\n"
            )
            if trend["axis_lifts"]:
                sys.stdout.write("Axes lifted: " + ", ".join(
                    f"{k}({v})" for k, v in trend["axis_lifts"].items()) + "\n")
        else:
            sys.stdout.write(json.dumps(trend, indent=2, ensure_ascii=True) + "\n")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
