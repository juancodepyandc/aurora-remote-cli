"""Experience the system accumulates, and how it changes future choices.

Verification says whether something worked. This is the other half: remembering
that it did, and letting the next attempt be different because of it.

The unit of learning is a *configuration attempt* — for a capability, a spec and
a set of settings, an outcome and, when it failed, a classified cause. From that
history this module derives two things the loop would not otherwise have:

  * a ranking that prefers configurations with a demonstrated success rate
    instead of trying the declared defaults first every time;
  * a set of configurations that already failed a specific way, which are
    skipped rather than re-attempted until something changes.

That is the whole claim, and it is testable: feed it failures and watch the
proposal change. It is not intelligence — it is the discipline of not repeating a
known mistake, which is the part of "learning" that is actually reachable without
inventing a mind.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .evolution import classify_failure


@dataclass(frozen=True)
class Attempt:
    """One recorded try: what was asked, what was used, what came back."""

    capability: str
    spec: str
    config: tuple          # the settings that define the attempt
    ok: bool
    cause: str = ""        # classified failure, empty when ok
    detail: str = ""
    at: float = 0.0

    def to_dict(self) -> dict:
        return {"capability": self.capability, "spec": self.spec,
                "config": list(self.config), "ok": self.ok, "cause": self.cause,
                "detail": self.detail, "at": self.at}


@dataclass
class Strategy:
    """What experience says about one configuration."""

    config: tuple
    tried: int = 0
    succeeded: int = 0
    causes: dict = field(default_factory=dict)

    @property
    def failures(self) -> int:
        return self.tried - self.succeeded

    @property
    def success_rate(self) -> float:
        """Unseen configurations are not failures: they score neutral, not zero."""
        if self.tried == 0:
            return 0.5
        return self.succeeded / self.tried

    def to_dict(self) -> dict:
        return {"config": list(self.config), "tried": self.tried,
                "succeeded": self.succeeded, "success_rate": round(self.success_rate, 4),
                "causes": dict(self.causes)}


class Experience:
    """Persistent store of attempts, and the policy it implies."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else None
        self.attempts: list[Attempt] = []
        self._strategies: dict = defaultdict(dict)
        if self.path and self.path.is_file():
            self._load()

    def _load(self) -> None:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for row in payload.get("attempts", []):
            self.attempts.append(Attempt(
                capability=row["capability"], spec=row["spec"],
                config=tuple(row["config"]), ok=bool(row["ok"]),
                cause=row.get("cause", ""), detail=row.get("detail", ""),
                at=row.get("at", 0.0),
            ))
            self._index(self.attempts[-1])

    def _index(self, attempt: Attempt) -> None:
        table = self._strategies[(attempt.capability, attempt.spec)]
        strategy = table.setdefault(attempt.config, Strategy(config=attempt.config))
        strategy.tried += 1
        if attempt.ok:
            strategy.succeeded += 1
        elif attempt.cause:
            strategy.causes[attempt.cause] = strategy.causes.get(attempt.cause, 0) + 1

    def record(self, capability: str, spec: str, config, ok: bool,
               detail: str = "", at: float | None = None) -> Attempt:
        """Log an outcome. A failure's cause is classified here, not by the caller."""
        cause = "" if ok else classify_failure(detail)
        attempt = Attempt(capability=capability, spec=spec, config=tuple(config),
                          ok=ok, cause=cause, detail=detail[:400],
                          at=at if at is not None else time.time())
        self.attempts.append(attempt)
        self._index(attempt)
        return attempt

    def strategies(self, capability: str, spec: str) -> list[Strategy]:
        table = self._strategies.get((capability, spec), {})
        return sorted(table.values(),
                      key=lambda s: (-s.success_rate, s.failures, s.tried, s.config))

    def excluded(self, capability: str, spec: str, *, min_tries: int = 3) -> set:
        """Configurations that failed the same way often enough to stop retrying.

        Retrying an identical configuration is only sensible while something that
        affects it might have changed. Measured on this machine: the fp16 octree
        rungs of the 3D ladder failed three times each with a flattened mesh, and
        each attempt costs minutes, so three identical failures is where re-trying
        stops being diligence and starts being waste. `HY3D`-scale hardware would
        raise it, and any successful attempt clears the configuration again.
        """
        blocked = set()
        for config, strategy in self._strategies.get((capability, spec), {}).items():
            if strategy.failures >= min_tries and strategy.succeeded == 0:
                blocked.add(config)
        return blocked

    def order(self, capability: str, spec: str, candidates) -> list:
        """Rank candidate configurations by what experience already knows.

        Unseen candidates are interleaved by their own order, so a fresh
        configuration is never buried behind a habit that worked once.
        """
        blocked = self.excluded(capability, spec)
        known = {s.config: s for s in self.strategies(capability, spec)}
        kept = [c for c in candidates if tuple(c) not in blocked]
        dropped = [c for c in candidates if tuple(c) in blocked]
        kept.sort(key=lambda c: (-known[tuple(c)].success_rate if tuple(c) in known else 1,
                                 known[tuple(c)].failures if tuple(c) in known else 0))
        return kept + dropped

    def reflect(self, capability: str, spec: str) -> dict:
        """Plain account of what has been learned, and what it implies."""
        ranked = self.strategies(capability, spec)
        best = ranked[0] if ranked else None
        lessons = []
        for strategy in ranked:
            if strategy.succeeded and strategy.failures:
                lessons.append(
                    f"configuration {list(strategy.config)} worked "
                    f"{strategy.succeeded}/{strategy.tried} times; keep it first")
            elif strategy.tried >= 2 and strategy.succeeded == 0:
                causes = ", ".join(f"{c} x{n}" for c, n in strategy.causes.items())
                lessons.append(
                    f"configuration {list(strategy.config)} never worked ({causes}); "
                    f"it is excluded until something about it changes")
        blocked = self.excluded(capability, spec)
        return {
            "capability": capability,
            "spec": spec,
            "attempts": len(self.attempts),
            "ranked": [s.to_dict() for s in ranked],
            "preferred": list(best.config) if best else None,
            "excluded": [list(c) for c in blocked],
            "lessons": lessons,
        }

    def save(self, path: Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("no path given and this store was created without one")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(
            {"version": 1, "attempts": [a.to_dict() for a in self.attempts]},
            indent=2, ensure_ascii=False), encoding="utf-8")
        return target
