"""Self-improving engine selection.

A capability is never pinned to one engine. This module keeps, for each
capability, a ranked set of candidates and promotes the best one that actually
proves itself on this machine:

    discover -> install -> implement adapter -> probe -> score -> promote

Every decision is appended to a JSONL decision log, including rejections.
Failures are classified and researched; suggested repairs are logged. The
injected attempt callback owns installation and execution. This loop does not
implement its suggested repairs or generate new adapters by itself.

Design rules that the rest of the codebase relies on:

* No candidate list is written in code. Candidates come from what is on the
  machine and from whatever a `Researcher` returns.
* No threshold is written in code. Promotion thresholds live in a policy file
  (`policies/quality.toml`) that a user can read and edit.
* Nothing is promoted without evidence: a probe must run and be scored.
* Nothing is ever removed here. Engine removal stays behind explicit consent in
  `engine_manager`.
"""
from __future__ import annotations

from copy import deepcopy
import json
import hashlib
import math
import os
import platform
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol

_POLICY_ENV = "JOBIA_QUALITY_POLICY"
_POLICY_RESOURCE = "policies/quality.toml"

# Failure classes the loop knows how to react to. A class with no reaction is
# still logged, which is what makes a stuck run diagnosable.
FAILURE_CLASSES = (
    "missing_dependency",
    "missing_weights",
    "unsupported_device",
    "out_of_memory",
    "bad_output",
    "degenerate_geometry",
    "timeout",
    "unknown",
)

# Reaction applied per failure class, as data rather than branching logic.
_DEFAULT_REACTIONS = {
    "missing_dependency": "install_dependency",
    "missing_weights": "fetch_weights",
    "unsupported_device": "fallback_backend",
    "out_of_memory": "reduce_preset",
    "bad_output": "tighten_quality_gate",
    # The geometry collapsed into a plate. The engine's own retry ladder already
    # varies resolution, precision and seed; the loop's lever is a different engine.
    "degenerate_geometry": "try_another_engine",
    "timeout": "raise_budget",
    "unknown": "research_cause",
}


def policy_path() -> Path:
    """Explicit override wins, then the packaged policy, then nothing."""
    override = os.environ.get(_POLICY_ENV)
    if override:
        return Path(override)
    try:
        from importlib.resources import files

        return Path(str(files("aurora_cli") / _POLICY_RESOURCE))
    except (ImportError, ModuleNotFoundError, TypeError):
        return Path(_POLICY_RESOURCE)


def load_policy() -> dict:
    """Read the promotion policy. Missing policy is an error, never a default.

    Silently falling back to built-in numbers is how a machine ends up with
    quality gates nobody chose, so an unreadable policy is raised instead.
    """
    path = policy_path()
    if not path.is_file():
        raise FileNotFoundError(f"quality policy not found at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:  # Python < 3.11
        import tomli

        return tomli.loads(text)


@dataclass(frozen=True)
class TaskSpec:
    """What a capability has to be able to do, in verifiable terms."""

    capability: str
    goal: str = ""
    #: Minimum accepted score for promotion, as a fraction of the best score
    #: ever observed for this capability. Policy sets the multiplier.
    required_checks: tuple[str, ...] = ("output_nonempty",)
    fixture: Path | None = None
    budget_s: int = 3600
    metric: str = "technical-readiness-v1"

    def key(self) -> str:
        return self.capability

    def context(self) -> dict:
        """Only compare scores for the same task, fixture and evaluator."""
        from .capability_probe import file_hash

        return {"goal": self.goal, "checks": sorted(self.required_checks),
                "fixture_sha256": file_hash(self.fixture) if self.fixture else None,
                "metric": self.metric}


@dataclass
class Candidate:
    """An engine that could serve a capability, plus where it came from."""

    name: str
    capability: str
    spec: str
    source: str  # "local" | "research" | "builtin"
    install_path: Path | None = None
    evidence: str = ""
    score: float | None = None
    checks: dict = field(default_factory=dict)
    adapter: dict = field(default_factory=dict)
    #: Path/hash of a retained manifest written by an actual candidate probe.
    proof: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["install_path"] = str(self.install_path) if self.install_path else None
        return data


class Researcher(Protocol):
    """Source of candidates that are not already installed.

    Kept as a protocol so the loop has no network dependency of its own and can
    be tested with a stub. Implementations are expected to do real lookups and
    to record what they consulted in `evidence`.
    """

    def candidates(self, task: TaskSpec, limit: int) -> list[Candidate]: ...

    def explain(self, failure: str, task: TaskSpec) -> str: ...


class NullResearcher:
    """Used when no researcher is wired: local discovery only.

    The loop still runs, it simply has nothing new to try, and it says so in the
    decision log instead of pretending the search succeeded.
    """

    def candidates(self, task: TaskSpec, limit: int) -> list[Candidate]:
        return []

    def explain(self, failure: str, task: TaskSpec) -> str:
        return "no researcher configured; failure not researched"


@dataclass
class Decision:
    """One entry of the audit trail."""

    at: float
    task: str
    step: str
    outcome: str
    detail: str = ""
    candidate: str = ""
    score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


class DecisionLog:
    """Append-only JSONL trail of every decision the loop makes."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: Decision) -> Decision:
        line = json.dumps(decision.to_dict(), ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        return decision

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]


def classify_failure(output: str) -> str:
    """Map a failed run to a failure class using evidence in the output."""
    text = (output or "").lower()
    if "no module named" in text or "importerror" in text or "modulenotfounderror" in text:
        return "missing_dependency"
    if "not found" in text and ("weight" in text or "checkpoint" in text or ".ckpt" in text or ".safetensors" in text):
        return "missing_weights"
    if "not implemented for" in text or "no kernel image" in text or "unsupported" in text:
        return "unsupported_device"
    if "out of memory" in text or "invalid buffer size" in text or "watermark" in text:
        return "out_of_memory"
    if "timed out" in text or "timeout" in text:
        return "timeout"
    if "returned nothing" in text or "produced no files" in text or "uniformly black" in text:
        return "bad_output"
    if "dégénérée" in text or "degenerate" in text or "bbox_fill" in text:
        return "degenerate_geometry"
    return "unknown"


class EvolutionLoop:
    """Keeps the best proven engine per capability and can replace it.

    The loop never raises on a failed candidate: it classifies the failure,
    asks the researcher for a cause, records the proposed reaction, and moves
    on. A run ends with either a promoted candidate or a log entry explaining
    why nothing could be promoted.
    """

    def __init__(
        self,
        manager,
        *,
        researcher: Researcher | None = None,
        state_path: Path | None = None,
        policy: dict | None = None,
    ):
        self.manager = manager
        self.researcher = researcher or NullResearcher()
        self.registry = manager.registry
        base = self.registry.registry_path.parent
        self.state_path = state_path or (base / "evolution.json")
        self.policy = policy if policy is not None else load_policy()
        from .platforms import detect
        from .core.machine import profile
        from .adapters import load_manifest
        machine = profile()
        self.environment = asdict(detect()) | {
            "os_release": platform.release(),
            "device_override": os.environ.get("JOBIA_DEVICE", ""),
            "total_ram_gb": machine.total_ram_gb,
            "vram_gb": machine.vram_gb,
            "cpu_cores": machine.cpu_cores,
            "accelerator": machine.accelerator,
            "unified_memory": machine.unified_memory,
        }
        self.adapter_signature = hashlib.sha256(
            json.dumps(load_manifest(), sort_keys=True).encode()).hexdigest()
        self.log = DecisionLog(base / "evolution.log")
        self.state = self._load_state()

    # -- state -------------------------------------------------------------
    def _load_state(self) -> dict:
        if self.state_path.is_file():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {"capabilities": {}}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(self.state_path)

    def current(self, capability: str) -> dict | None:
        return self.state["capabilities"].get(capability)

    def recommendation(self, capability: str) -> Candidate | None:
        """A retained readiness result is a routing hint, not proof for a new job.

        Production must still check this job's output and its current resource
        budget. Legacy, stale or changed execution evidence is not reused.
        """
        from .capability_probe import evaluation_profile

        current = self.current(capability)
        if not current:
            return None
        context = current.get("context", {})
        if (json.dumps(context.get("environment"), sort_keys=True)
                != json.dumps(self.environment, sort_keys=True)
                or context.get("adapters_sha256") != self.adapter_signature):
            return None
        evaluation = evaluation_profile(capability, self.policy)
        if context.get("evaluation_policy") != evaluation:
            return None
        promotion = self.policy["promotion"]
        if (time.time() - current.get("verified_at", current.get("promoted_at", 0)) > promotion["evidence_max_age_s"]
                or current.get("score", 0) < promotion["absolute_min_score"]):
            return None
        record = self.registry.get(current["engine"])
        if record is None or not record.install_path.is_dir():
            return None
        candidate = Candidate(
            current["engine"], capability, current["spec"], current["source"],
            install_path=record.install_path, score=current["score"],
            checks=current["checks"], adapter=record.metadata.get("adapter", {}),
            proof=current["proof"],
        )
        task = TaskSpec(capability, goal=context.get("goal", ""),
                        required_checks=tuple(evaluation["required_checks"]),
                        metric=evaluation["metric"])
        if any(candidate.checks.get(check) is not True for check in task.required_checks):
            return None
        verified, proof = verify_probe_evidence(candidate, task)
        if not verified or proof.get("fixture_sha256") != context.get("fixture_sha256"):
            return None
        return candidate

    def _context(self, task: TaskSpec) -> dict:
        return task.context() | {
            "environment": self.environment,
            "adapters_sha256": self.adapter_signature,
            "evaluation_policy": self.policy.get("evaluation", {}).get(task.capability, {}),
        }

    def _incumbent(self, task: TaskSpec) -> dict | None:
        context = self._context(task)
        key = hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
        current = self.state.get("evaluations", {}).get(task.key(), {}).get(key)
        if not current:
            return None
        record = self.registry.get(current["engine"])
        if record is None:
            return None
        previous = Candidate(
            current["engine"], task.capability, current["spec"], current["source"],
            install_path=record.install_path, score=current["score"],
            checks=current["checks"], proof=current["proof"],
        )
        verified, _ = verify_probe_evidence(previous, task)
        return current if verified else None

    def _refresh_incumbent(self, task: TaskSpec, candidate: Candidate) -> bool:
        """Fresh identical performance renews evidence without claiming progress."""
        current = self._incumbent(task)
        if (current is None or current["engine"] != candidate.name
                or current["spec"] != candidate.spec or current["score"] != candidate.score
                or any(candidate.checks.get(check) is not True for check in task.required_checks)):
            return False
        verified, _ = verify_probe_evidence(candidate, task)
        if not verified:
            return False
        entry = current | {"proof": candidate.proof, "checks": candidate.checks,
                           "verified_at": time.time()}
        record = self.registry.get(candidate.name)
        record.metadata.update(proof=candidate.proof, checks=candidate.checks,
                               evidence=candidate.evidence)
        self.registry.add(record)
        key = hashlib.sha256(json.dumps(entry["context"], sort_keys=True).encode()).hexdigest()
        self.state["evaluations"][task.key()][key] = entry
        if self.current(task.key()).get("context") == current["context"]:
            self.state["capabilities"][task.key()] = entry
        self._save_state()
        self._record(task.key(), "verify", "refreshed", "unchanged score; fresh execution evidence",
                     candidate.name, candidate.score)
        return True

    def _record(self, task: str, step: str, outcome: str, detail: str = "", candidate: str = "", score: float | None = None) -> None:
        self.log.record(Decision(at=time.time(), task=task, step=step, outcome=outcome, detail=detail, candidate=candidate, score=score))

    # -- discovery ---------------------------------------------------------
    def discover(self, task: TaskSpec) -> list[Candidate]:
        """Local engines first, then whatever research turns up.

        Local results win ties because they are already installed and therefore
        cost nothing to prove.
        """
        limit = int(self.policy.get("discovery", {}).get("research_limit", 5))
        found: list[Candidate] = []
        # Refresh the registry from disk so an engine installed outside JOBIA is
        # still considered rather than silently ignored.
        try:
            self.manager.scan_local_installations()
        except Exception as exc:
            self._record(task.key(), "discover", "degraded", f"local scan failed: {exc}")
        for record in self.registry.list_by_capability(task.capability):
            found.append(Candidate(
                name=record.name,
                capability=task.capability,
                spec=str(record.metadata.get("spec") or record.metadata.get("repo_id")
                         or record.metadata.get("model_name") or record.name),
                source="local",
                install_path=record.install_path,
                evidence="already registered on this machine",
                adapter=dict(record.metadata.get("adapter", {})),
            ))
        try:
            researched = self.researcher.candidates(task, limit)
        except Exception as exc:
            researched = []
            self._record(task.key(), "discover", "degraded", f"research unavailable: {exc}")
        for candidate in researched:
            candidate.source = candidate.source or "research"
            found.append(candidate)
        if self.policy.get("discovery", {}).get("prefer_local", True):
            found.sort(key=lambda candidate: candidate.source != "local")
        self._record(task.key(), "discover", "ok", f"{len(found)} candidate(s)")
        return found

    # -- promotion ---------------------------------------------------------
    def should_promote(self, task: TaskSpec, candidate: Candidate) -> tuple[bool, str]:
        """Policy decides, evidence must exist. No candidate, no promotion."""
        promo = self.policy.get("promotion", {})
        if candidate.score is None:
            return False, "candidate was never scored"
        if (type(candidate.score) not in (float, int) or not math.isfinite(candidate.score)
                or not 0 <= candidate.score <= 1):
            return False, "candidate score must be finite and between 0 and 1"
        for check in task.required_checks:
            if candidate.checks.get(check) is not True:
                return False, f"required check failed: {check}"
        floor = float(promo.get("absolute_min_score", 0.0))
        if candidate.score < floor:
            return False, f"score {candidate.score:.3f} below absolute floor {floor}"
        verified, proof_or_reason = verify_probe_evidence(candidate, task)
        if not verified:
            return False, str(proof_or_reason)
        current = self._incumbent(task)
        if current is None:
            return True, "first proven candidate for this evaluation context"
        best = float((current or {}).get("score", 0.0))
        ratio = float(promo.get("replacement_ratio", 1.0))
        required = best * ratio
        if candidate.score <= best:
            return False, (f"score {candidate.score:.3f} below or equal to current {best:.3f}; "
                           "replacement must be strictly better")
        if candidate.score >= required:
            return True, f"score {candidate.score:.3f} >= {required:.3f} (current {best:.3f} x {ratio})"
        return False, f"score {candidate.score:.3f} below {required:.3f} needed to replace {best:.3f}"

    def promote(self, task: TaskSpec, candidate: Candidate, reason: str) -> None:
        from .engine_manager import EngineRecord

        allowed, gate_reason = self.should_promote(task, candidate)
        if not allowed:
            raise ValueError(f"Promotion refused: {gate_reason}")

        record = EngineRecord(
            name=candidate.name,
            capability=candidate.capability,
            install_path=candidate.install_path or Path("."),
            source=candidate.source,
            installed_at=time.time(),
            metadata={
                "spec": candidate.spec,
                "score": candidate.score,
                "checks": candidate.checks,
                "adapter": candidate.adapter,
                "evidence": candidate.evidence,
                "proof": candidate.proof,
                "score_kind": task.metric,
            },
        )
        self.registry.add(record)
        entry = {
            "engine": candidate.name,
            "spec": candidate.spec,
            "source": candidate.source,
            "score": candidate.score,
            "checks": candidate.checks,
            "promoted_at": time.time(),
            "verified_at": time.time(),
            "reason": reason,
            "proof": candidate.proof,
            "score_kind": task.metric,
            "context": self._context(task),
        }
        self.state["capabilities"][task.key()] = entry
        key = hashlib.sha256(json.dumps(entry["context"], sort_keys=True).encode()).hexdigest()
        self.state.setdefault("evaluations", {}).setdefault(task.key(), {})[key] = entry
        self._save_state()
        self._record(task.key(), "promote", "ok", reason, candidate.name, candidate.score)

    # -- the cycle ---------------------------------------------------------
    def improve(self, task: TaskSpec, *, attempt) -> dict:
        """Run the full cycle. `attempt(candidate)` installs and probes.

        `attempt` is injected so the loop owns the policy and the audit trail
        while the caller owns the heavy lifting, and so tests can drive the
        loop without any model installed.

        Contract for `attempt`: it must prove the candidate on this machine and
        record the proof on the candidate itself — `candidate.score` (0..1) and
        one entry per check in `task.required_checks` in `candidate.checks`. A
        candidate left unscored or unchecked is refused by the gate, so a
        partially-implemented adapter cannot be promoted by accident.
        """
        attempts = int(self.policy.get("retry", {}).get("max_attempts", 3))
        best: Candidate | None = None
        last_detail = ""
        attempted: set[tuple] = set()

        for round_index in range(attempts):
            for candidate in self.discover(task):
                identity = (candidate.spec, str(candidate.install_path),
                            json.dumps(candidate.adapter, sort_keys=True))
                if identity in attempted:
                    continue
                attempted.add(identity)
                candidate.score, candidate.checks, candidate.proof = None, {}, {}
                try:
                    attempt(candidate)
                    attempted.add((candidate.spec, str(candidate.install_path),
                                   json.dumps(candidate.adapter, sort_keys=True)))
                except Exception as exc:  # a failing candidate is data, not a crash
                    failure = classify_failure(str(exc))
                    reaction = _DEFAULT_REACTIONS.get(failure, "research_cause")
                    try:
                        cause = self.researcher.explain(str(exc), task)
                    except Exception as research_error:
                        cause = f"research unavailable: {research_error}"
                    detail = f"{failure}; proposed reaction={reaction} (not executed); cause: {cause}"
                    self._record(task.key(), "attempt", "failed", detail, candidate.name)
                    last_detail = detail
                    continue
                ok, reason = self.should_promote(task, candidate)
                if ok:
                    self._record(task.key(), "gate", "accepted", reason, candidate.name, candidate.score)
                    if best is None or candidate.score > best.score:
                        best = deepcopy(candidate)
                    continue
                last_detail = reason
                self._record(task.key(), "gate", "rejected", reason, candidate.name, candidate.score)
                self._refresh_incumbent(task, candidate)

        if best is not None:
            # Discovery order does not decide the winner. Promote once, after
            # every distinct candidate in the bounded search has been judged.
            ok, reason = self.should_promote(task, best)
            if ok:
                self.promote(task, best, reason)
                return {"promoted": best.name, "reason": reason, "rounds": attempts}
            last_detail = reason

        self._record(task.key(), "finish", "unpromoted", last_detail or "no candidate proved itself")
        return {"promoted": None, "reason": last_detail or "no candidate proved itself", "rounds": attempts}


def verify_probe_evidence(candidate: Candidate, task: TaskSpec) -> tuple[bool, dict | str]:
    """Verify retained output, identity and score before accepting a probe claim."""
    from .capability_probe import file_hash

    try:
        manifest = Path(candidate.proof["manifest"])
        if file_hash(manifest) != candidate.proof["sha256"]:
            return False, "probe evidence manifest changed"
        proof = json.loads(manifest.read_text(encoding="utf-8"))
        if (proof.get("kind") != "candidate-generation"
                or proof.get("candidate") != candidate.name
                or proof.get("capability") != task.capability
                or candidate.capability != task.capability
                or proof.get("spec") != candidate.spec
                or candidate.install_path is None
                or proof.get("install_path") != str(candidate.install_path.resolve())
                or proof.get("score") != candidate.score
                or proof.get("checks") != candidate.checks
                or proof.get("metric") != task.metric
                or proof.get("execution", {}).get("model_ref") != candidate.spec):
            return False, "probe evidence belongs to a different candidate or execution"
        output = Path(proof["output"])
        if not output.is_file() or output.stat().st_size == 0 or file_hash(output) != proof["sha256"]:
            return False, "probe output is absent, empty or changed"
        if task.fixture and proof.get("fixture_sha256") != file_hash(task.fixture):
            return False, "probe evidence used a different task fixture"
        if task.goal and proof.get("execution", {}).get("prompt") != task.goal:
            return False, "probe evidence used a different task goal"
        return True, proof
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        return False, f"candidate has no verifiable execution evidence: {exc}"
