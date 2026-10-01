"""The self-improving loop: discovery, scoring, promotion and failure handling.

The loop's whole value is that it replaces an engine on its own, so the tests
focus on the two things that must never be wrong: it cannot promote something
unproven, and it cannot crash on a failing candidate.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.engine_manager import AutonomousEngineManager, EngineRecord
from aurora_cli.evolution import (
    Candidate,
    DecisionLog,
    EvolutionLoop,
    NullResearcher,
    TaskSpec,
    classify_failure,
    load_policy,
    policy_path,
)

# The shipped policy, loaded rather than copied. A hardcoded copy silently drifts
# the moment someone edits quality.toml, and the tests then pass against
# thresholds the product does not use.
POLICY = load_policy()
FLOOR = POLICY["promotion"]["absolute_min_score"]
ABOVE_FLOOR = min(1.0, FLOOR + 0.15)
BELOW_FLOOR = max(0.0, FLOOR - 0.15)


@pytest.fixture
def manager(tmp_path):
    mgr = AutonomousEngineManager()
    # The manager loads the real registry at construction, so start from empty or
    # these tests would assert against whatever happens to be installed locally.
    mgr.registry._records = {}
    mgr.registry.registry_path = tmp_path / "engine_registry.json"
    mgr.registry._save()
    mgr.scan_local_installations = lambda: 0
    return mgr


@pytest.fixture
def task():
    return TaskSpec(capability="3d", required_checks=("output_nonempty",), budget_s=60)


def make_loop(manager, *, researcher=None, policy=None, tmp_path=None):
    return EvolutionLoop(manager, researcher=researcher, policy=policy or POLICY,
                         state_path=tmp_path / "evolution.json")


def candidate(name, score=None, checks=None, spec="spec", tmp_path=None, with_proof=True):
    """A candidate carrying a real proof manifest, because the gate demands one.

    Building the manifest here rather than stubbing the verifier keeps the tests
    honest about what promotion actually requires.
    """
    cand = Candidate(name=name, capability="3d", spec=spec, source="local",
                     install_path=Path("/tmp") / name, score=score,
                     checks=checks if checks is not None else {"output_nonempty": True})
    if not with_proof:
        return cand
    from aurora_cli.capability_probe import file_hash

    directory = Path(tmp_path or "/tmp")
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{name}.glb"
    output.write_bytes(b"glTF fake but non-empty output")
    proof = {
        "schema": 1, "kind": "candidate-generation",
        "candidate": cand.name, "capability": cand.capability,
        "spec": cand.spec, "install_path": str(cand.install_path.resolve()),
        "output": str(output.resolve()), "sha256": file_hash(output),
        "checks": cand.checks, "score": cand.score,
        "metric": "technical-readiness-v1",
        "execution": {"model_ref": cand.spec},
        "fixture_sha256": None,
    }
    manifest = directory / f"{name}.proof.json"
    manifest.write_text(json.dumps(proof), encoding="utf-8")
    cand.proof = {"manifest": str(manifest), "sha256": file_hash(manifest)}
    return cand


class TestPolicy:
    def test_packaged_policy_is_readable(self):
        assert policy_path().is_file()
        assert load_policy()["promotion"]["absolute_min_score"] > 0

    def test_missing_policy_is_an_error_not_a_silent_default(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JOBIA_QUALITY_POLICY", str(tmp_path / "absent.toml"))
        with pytest.raises(FileNotFoundError):
            load_policy()


class TestFailureClassification:
    @pytest.mark.parametrize("output,expected", [
        ("ModuleNotFoundError: No module named 'bpy'", "missing_dependency"),
        ("checkpoint /weights/model.ckpt not found", "missing_weights"),
        ("kernel image is not implemented for MPS", "unsupported_device"),
        ("RuntimeError: invalid buffer size", "out_of_memory"),
        ("the operation timed out", "timeout"),
        ("the paint pipeline produced no files", "bad_output"),
        ("something nobody anticipated", "unknown"),
    ])
    def test_evidence_maps_to_class(self, output, expected):
        assert classify_failure(output) == expected


class TestPromotionGate:
    def test_uncored_candidate_is_never_promoted(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        ok, reason = loop.should_promote(task, candidate("engine-a", score=None, tmp_path=tmp_path))
        assert not ok and "never scored" in reason

    def test_failed_required_check_blocks_promotion(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        bad = candidate("engine-a", score=0.99, checks={"output_nonempty": False}, tmp_path=tmp_path)
        ok, reason = loop.should_promote(task, bad)
        assert not ok and "required check" in reason

    def test_score_below_absolute_floor_is_refused(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        ok, reason = loop.should_promote(task, candidate("weak", score=BELOW_FLOOR, tmp_path=tmp_path))
        assert not ok and "absolute floor" in reason

    def test_first_proven_candidate_is_promoted(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        ok, _ = loop.should_promote(task, candidate("good", score=ABOVE_FLOOR, tmp_path=tmp_path))
        assert ok

    def test_weaker_candidate_cannot_replace_incumbent(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        loop.promote(task, candidate("incumbent", score=min(1.0, ABOVE_FLOOR + 0.1), tmp_path=tmp_path), "first")
        ok, reason = loop.should_promote(task, candidate("worse", score=ABOVE_FLOOR - 0.05, tmp_path=tmp_path))
        assert not ok and "below" in reason

    def test_strictly_better_candidate_replaces_incumbent(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        loop.promote(task, candidate("incumbent", score=ABOVE_FLOOR, tmp_path=tmp_path), "first")
        better = candidate("better", score=min(1.0, ABOVE_FLOOR + 0.2), tmp_path=tmp_path)
        ok, _ = loop.should_promote(task, better)
        assert ok
        loop.promote(task, better, "better")
        assert loop.current("3d")["engine"] == "better"


def prove(cand, checks=None):
    """What a real adapter does after it runs.

    Delegates to the production evidence writer rather than imitating it, so the
    promotion gate is exercised against the manifest format the probes really
    write, including its hash chain.
    """
    import time as _time

    from aurora_cli.capability_probe import _record

    checks = checks or {"output_nonempty": True, "mesh_valid": True, "has_texture": True}
    directory = Path(cand.install_path)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{cand.name}.glb"
    output.write_bytes(b"glTF fake but non-empty output")
    return _record(cand, output, directory, _time.monotonic(), checks,
                   {"model_ref": cand.spec})


class TestLoop:
    def test_promotes_the_proven_candidate(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        manager.registry.add(EngineRecord("engine-a", "3d", Path("/tmp/a"), "local", 0.0))
        result = loop.improve(task, attempt=prove)
        assert result["promoted"] == "engine-a"
        assert loop.current("3d")["engine"] == "engine-a"

    def test_candidate_left_unproven_is_refused(self, manager, task, tmp_path):
        """A half-implemented adapter must not be promotable by accident."""
        loop = make_loop(manager, tmp_path=tmp_path)
        manager.registry.add(EngineRecord("engine-a", "3d", Path("/tmp/a"), "local", 0.0))
        result = loop.improve(task, attempt=lambda c: prove(c, {"output_nonempty": True, "mesh_valid": True, "has_texture": False}))
        assert result["promoted"] is None

    def test_failing_candidate_does_not_crash_the_loop(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)

        def attempt(cand):
            if cand.name == "broken":
                raise RuntimeError("No module named 'bpy'")
            prove(cand)

        manager.registry.add(EngineRecord("broken", "3d", Path("/tmp/broken"), "local", 0.0))
        manager.registry.add(EngineRecord("working", "3d", Path("/tmp/working"), "local", 0.0))
        result = loop.improve(task, attempt=attempt)
        assert result["promoted"] == "working"
        failures = [e for e in loop.log.entries() if e["step"] == "attempt" and e["outcome"] == "failed"]
        assert failures and "missing_dependency" in failures[0]["detail"]

    def test_research_candidates_are_considered(self, manager, task, tmp_path):
        class Researcher(NullResearcher):
            def candidates(self, task, limit):
                return [Candidate("from-research", "3d", "best/model", "research",
                                  install_path=Path("/tmp/research"))]

            def explain(self, failure, task):
                return "looked it up"

        loop = make_loop(manager, researcher=Researcher(), tmp_path=tmp_path)
        result = loop.improve(task, attempt=prove)
        assert result["promoted"] == "from-research"

    def test_nothing_promoted_is_reported_not_raised(self, manager, task, tmp_path):
        loop = make_loop(manager, tmp_path=tmp_path)
        manager.registry.add(EngineRecord("weak", "3d", Path("/tmp/weak"), "local", 0.0))
        result = loop.improve(task, attempt=lambda c: prove(c, {"output_nonempty": True, "mesh_valid": False, "has_texture": False}))
        assert result["promoted"] is None
        assert result["reason"]

    def test_state_survives_a_new_loop_instance(self, manager, task, tmp_path):
        first = make_loop(manager, tmp_path=tmp_path)
        first.promote(task, candidate("engine-a", score=ABOVE_FLOOR, tmp_path=tmp_path), "first")
        second = make_loop(manager, tmp_path=tmp_path)
        assert second.current("3d")["engine"] == "engine-a"


class TestDecisionLog:
    def test_log_is_append_only_jsonl(self, tmp_path):
        log = DecisionLog(tmp_path / "evolution.log")
        from aurora_cli.evolution import Decision
        log.record(Decision(at=1.0, task="3d", step="discover", outcome="ok", detail="1 candidate(s)"))
        log.record(Decision(at=2.0, task="3d", step="promote", outcome="ok", detail="first"))
        lines = (tmp_path / "evolution.log").read_text().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[1])["step"] == "promote"
        assert len(log.entries()) == 2

    def test_missing_log_reads_as_empty(self, tmp_path):
        assert DecisionLog(tmp_path / "absent.log").entries() == []


class TestDegeneracyIsClassified:
    """The loop must recognise its own failures, or it cannot react to them."""

    def test_flattened_geometry_is_its_own_class(self):
        from aurora_cli.evolution import classify_failure

        assert classify_failure("reconstruction dégénérée : bbox_fill < 0.05") == "degenerate_geometry"

    def test_degeneracy_has_a_concrete_reaction(self):
        from aurora_cli.evolution import _DEFAULT_REACTIONS

        assert _DEFAULT_REACTIONS["degenerate_geometry"] == "try_another_engine"

    def test_it_does_not_shadow_the_other_classes(self):
        from aurora_cli.evolution import classify_failure

        assert classify_failure("No module named 'bpy'") == "missing_dependency"
        assert classify_failure("invalid buffer size") == "out_of_memory"
