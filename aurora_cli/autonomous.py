"""Run bounded capability improvement until no verified improvement is found.

Stability applies to the candidates and evaluators available during this run.
It does not prove a global optimum, especially when research is unavailable or
a capability has no implemented evaluator. Bounds come from policy.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import AdapterRegistry
from .engine_manager import AutonomousEngineManager
from .evolution import EvolutionLoop, TaskSpec


@dataclass
class PassResult:
    index: int
    promoted: dict = field(default_factory=dict)
    blocked: dict = field(default_factory=dict)
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"index": self.index, "promoted": self.promoted,
                "blocked": self.blocked, "seconds": round(self.seconds, 2)}


@dataclass
class RunReport:
    passes: list = field(default_factory=list)
    stable_at: int | None = None
    reason: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def promotions(self) -> dict:
        """Final promoted engine per capability."""
        chosen = {}
        for entry in self.passes:
            chosen.update(entry.promoted)
        return chosen

    @property
    def blocked(self) -> dict:
        reasons = {}
        for entry in self.passes:
            reasons.update(entry.blocked)
        return reasons

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": time.time(),
            "stable_at_pass": self.stable_at,
            "reason": self.reason,
            "promotions": self.promotions,
            "blocked": self.blocked,
            "passes": [p.to_dict() for p in self.passes],
        }


class AutonomousRun:
    """Drives improvement for a set of capabilities without ever asking."""

    def __init__(self, *, researcher=None, policy: dict | None = None,
                 state_dir: Path | None = None, log=None):
        self.manager = AutonomousEngineManager()
        self.registry = AdapterRegistry()
        base = state_dir or self.manager.registry.registry_path.parent
        self.loop = EvolutionLoop(self.manager, researcher=researcher, policy=policy,
                                  state_path=base / "evolution.json")
        self.log = log or (lambda message: None)
        bounds = self.loop.policy.get("autonomy", {})
        self.max_passes = int(bounds.get("max_passes", 3))
        self.stability_rounds = int(bounds.get("stability_rounds", 1))

    def run(self, tasks: list[TaskSpec]) -> RunReport:
        report = RunReport()
        quiet = 0

        for index in range(1, self.max_passes + 1):
            entry = PassResult(index=index)
            started = time.time()
            for task in tasks:
                outcome = self.loop.improve(task, attempt=self._attempt_for(task))
                if outcome["promoted"]:
                    entry.promoted[task.key()] = {
                        "engine": outcome["promoted"],
                        "reason": outcome["reason"],
                    }
                    self.log(f"pass {index}: {task.key()} -> {outcome['promoted']}")
                else:
                    entry.blocked[task.key()] = outcome["reason"]
                    self.log(f"pass {index}: {task.key()} bloqué ({outcome['reason'][:80]})")
            entry.seconds = time.time() - started
            report.passes.append(entry)

            if entry.promoted:
                quiet = 0
                continue

            quiet += 1
            if quiet >= self.stability_rounds:
                report.stable_at = index
                report.reason = (
                    f"aucune promotion sur {quiet} passe(s) consécutive(s) : "
                    f"l'état observé est stable pour cette recherche bornée"
                )
                self.loop._record("autonomy", "stop", "stable", report.reason)
                return report

        report.reason = f"plafond de {self.max_passes} passes atteint sans stabilisation"
        self.loop._record("autonomy", "stop", "capped", report.reason)
        return report

    def _attempt_for(self, task: TaskSpec):
        """Bind a capability's real probe to the loop's attempt contract."""
        from .capability_probe import run_probe

        def attempt(candidate):
            runner, reason = self.registry.resolve(task.capability, candidate.spec)
            if runner is None:
                # Saying so up front is the difference between "unsupported here"
                # and a stack trace from a runner that was never written.
                raise RuntimeError(reason)
            candidate.adapter = dict(runner.to_dict(), runner=runner.id, env=runner.environment())
            kwargs = {"fixture": task.fixture}
            if task.goal:
                kwargs["prompt"] = task.goal
            return run_probe(task.capability, candidate, **kwargs)

        return attempt


def run_autonomous(capabilities: list[dict], *, researcher=None, log=None) -> dict:
    """Entry point: run to a fixed point and return a report, asking nothing."""
    from .capability_probe import evaluation_profile

    tasks = []
    for item in capabilities:
        evaluation = evaluation_profile(item["capability"])
        tasks.append(TaskSpec(
            capability=item["capability"], goal=item.get("goal", ""),
            required_checks=tuple(item.get("required_checks", evaluation["required_checks"])),
            fixture=Path(item["fixture"]) if item.get("fixture") else None,
            metric=evaluation["metric"],
        ))
    runner = AutonomousRun(researcher=researcher, log=log)
    return runner.run(tasks).to_dict()
