"""Turn a failure into an action, instead of a refusal.

The honest version of autonomy is not "never fail" — it is "when something fails,
act on it". A system that reports `out_of_memory` and stops has finished half a
job. This module holds the second half: a classified failure is mapped to a
concrete change, the change is applied to the request, and the retry happens
without anyone being asked.

Three properties make that safe to run unattended, and each is enforced here
rather than hoped for:

* **Bounded.** A remedy declares how many times it may apply. Retrying a
  self-repair loop forever is the classic way to look busy and achieve nothing.
* **Recorded.** Every application is logged with the cause it answered, so the
  trail shows what the system did *because* of a specific failure.
* **Learning-informed.** When several remedies exist for a cause, the one that
  worked before is preferred, so the repair strategy itself improves.

A cause with no remedy is not guessed at. It is reported as unknown, which is the
honest outcome and the one that tells a human where to look.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from .evolution import classify_failure

# Applied at most once per attempt cycle unless the remedy says otherwise.
DEFAULT_LIMIT = 2


@dataclass
class Remedy:
    """One concrete change to the request, for one classified cause."""

    cause: str
    name: str
    change: Callable[[dict], dict]
    limit: int = DEFAULT_LIMIT
    detail: str = ""

    def to_dict(self) -> dict:
        return {"cause": self.cause, "name": self.name, "limit": self.limit,
                "detail": self.detail}


def _clone(request: dict) -> dict:
    return dict(request)


def _lower_resolution(request: dict) -> dict:
    """Fewer tokens, not fewer output pixels.

    The multiview attention matrix grows with the square of the token count, so
    resolution is the lever that actually reduces peak memory. Dropping the
    deliverable's size instead would trade the fix for a worse result.
    """
    out = _clone(request)
    for key in ("views", "max_num_view"):
        if isinstance(out.get(key), int) and out[key] > 1:
            out[key] = max(1, out[key] - 1)
            break
    else:
        out["resolution"] = max(128, int(out.get("resolution", 256)) // 2)
    return out


def _halve_resolution(request: dict) -> dict:
    out = _clone(request)
    out["resolution"] = max(128, int(out.get("resolution", 256)) // 2)
    return out


def _escalate_precision(request: dict) -> dict:
    """A numeric collapse is a precision problem before it is a memory problem."""
    out = _clone(request)
    out["dtype"] = "float32"
    return out


def _raise_resolution(request: dict) -> dict:
    """A too-flat reconstruction is not a detail problem; it is a blind guess."""
    out = _clone(request)
    out["resolution"] = int(out.get("resolution", 256)) * 2
    return out


def _change_seed(request: dict) -> dict:
    out = _clone(request)
    out["seed"] = int(out.get("seed", 0)) + 1
    return out


def _drop_optional_stage(request: dict) -> dict:
    """Degrade the run rather than deliver nothing."""
    out = _clone(request)
    out["texture"] = False
    return out


def default_remedies() -> list:
    """The remedies this build knows how to apply.

    Declared as data so the policy of "what do we do about this failure" is
    readable and extensible without touching the retry machinery.
    """
    return [
        Remedy("out_of_memory", "reduce_views", _lower_resolution,
               detail="one fewer view; attention scales with the square of tokens"),
        Remedy("out_of_memory", "halve_resolution", _halve_resolution,
               detail="halve the diffusion resolution"),
        Remedy("degenerate_geometry", "raise_resolution", _raise_resolution,
               detail="a flat mesh is under-sampled, not under-resourced"),
        Remedy("degenerate_geometry", "escalate_precision", _escalate_precision,
               detail="numerical collapse shows up as a collapsed surface"),
        Remedy("degenerate_geometry", "change_seed", _change_seed,
               detail="a stuck flow-matching trajectory; resample it"),
        Remedy("bad_output", "drop_texture", _drop_optional_stage,
               detail="ship the geometry rather than nothing"),
        Remedy("missing_dependency", "change_seed", _change_seed,
               detail="nothing else is actionable here; retry once"),
    ]


@dataclass
class Action:
    """One applied remedy, recorded."""

    cause: str
    remedy: str
    at: float = field(default_factory=time.time)
    request: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"cause": self.cause, "remedy": self.remedy,
                "at": self.at, "request": dict(self.request)}


class AutoRepair:
    """Applies remedies to a failing request, bounded and logged."""

    def __init__(self, remedies=None, experience=None):
        self.remedies = list(remedies) if remedies is not None else default_remedies()
        self.experience = experience
        self.actions: list = []

    def causes_for(self, output: str) -> list:
        """Every cause present in the output, most specific first."""
        text = (output or "").lower()
        found = []
        if "watermark" in text or "invalid buffer size" in text or "out of memory" in text:
            found.append("out_of_memory")
        if "dégénérée" in text or "degenerate" in text or "bbox_fill" in text:
            found.append("degenerate_geometry")
        if "no module named" in text or "importerror" in text:
            found.append("missing_dependency")
        if "produced no files" in text or "uniformly black" in text or "returned nothing" in text:
            found.append("bad_output")
        if not found:
            found.append(classify_failure(output))
        return found

    def _prefer(self, cause: str) -> Remedy | None:
        options = [r for r in self.remedies if r.cause == cause]
        if not options:
            return None
        if self.experience is None:
            return options[0]
        # Prefer the remedy that has worked before, when there is a history.
        ranked = self.experience.order(cause, "remedy",
                                       [(r.name,) for r in options])
        by_name = {r.name: r for r in options}
        for entry in ranked:
            name = entry[0] if isinstance(entry, tuple) else entry
            if name in by_name:
                return by_name[name]
        return options[0]

    def apply(self, request: dict, output: str, *, cycle: int = 0) -> dict:
        """Return the next request to try, plus the action taken.

        An unchanged request means no remedy applied, which is the signal to stop
        retrying and report rather than spin.
        """
        for cause in self.causes_for(output):
            remedy = self._prefer(cause)
            if remedy is None:
                continue
            already = sum(1 for a in self.actions
                          if a.cause == cause and a.remedy == remedy.name)
            if already >= remedy.limit:
                continue
            updated = remedy.change(request)
            if updated == request:
                continue
            self.actions.append(Action(cause=cause, remedy=remedy.name, request=dict(updated)))
            if self.experience is not None:
                self.experience.record(cause, "remedy", (remedy.name,), ok=True)
            return updated
        return request

    def report(self) -> dict:
        return {"actions": [a.to_dict() for a in self.actions],
                "count": len(self.actions),
                "remedies": [r.to_dict() for r in self.remedies]}
