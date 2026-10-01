"""The self-driving loop: it converges, and it never asks.

Termination is the property that matters. A loop that retries forever is not
autonomy, it is a hang. These tests pin down both halves: it stops at a proven
fixed point, and it keeps going while progress is still being made.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.autonomous import AutonomousRun, PassResult, RunReport, run_autonomous
from aurora_cli.evolution import EvolutionLoop, TaskSpec, load_policy

POLICY = load_policy()
TASK = TaskSpec(capability="3d", required_checks=("output_nonempty",))


class FakeLoop:
    """Stands in for EvolutionLoop so the loop's control flow is testable alone."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.stopped = []

    def improve(self, task, attempt):
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome

    def _record(self, *args, **kwargs):
        self.stopped.append(args)


def make_run(outcomes, max_passes=3, stability_rounds=1):
    policy = {**POLICY, "autonomy": {"max_passes": max_passes,
                                     "stability_rounds": stability_rounds}}
    runner = AutonomousRun(policy=policy)
    runner.loop = FakeLoop(outcomes)
    return runner


class TestTermination:
    def test_it_stops_once_a_pass_promotes_nothing(self):
        runner = make_run([{"promoted": None, "reason": "aucun candidat"}])
        report = runner.run([TASK])
        assert report.stable_at == 1
        assert "stable" in report.reason

    def test_it_keeps_going_while_progress_happens(self):
        outcomes = [
            {"promoted": "a", "reason": "premier"},
            {"promoted": "b", "reason": "meilleur"},
            {"promoted": None, "reason": "plus rien"},
        ]
        report = make_run(outcomes).run([TASK])
        assert [p.index for p in report.passes] == [1, 2, 3]
        assert report.stable_at == 3

    def test_a_promotion_resets_the_quiet_counter(self):
        """Progress must not be forgotten because of an earlier quiet pass.

        Needs two quiet rounds to be observable: with stability_rounds=1 the very
        first quiet pass is already a fixed point, so the reset is unreachable.
        """
        outcomes = [
            {"promoted": None, "reason": "rien"},
            {"promoted": "a", "reason": "premier"},
            {"promoted": None, "reason": "rien"},
            {"promoted": None, "reason": "rien"},
        ]
        report = make_run(outcomes, max_passes=4, stability_rounds=2).run([TASK])
        # The promotion on pass 2 cleared the counter, so it took two more quiet
        # passes (3 and 4) to call the state stable.
        assert report.stable_at == 4

    def test_two_quiet_passes_in_a_row_stabilise_when_configured(self):
        outcomes = [{"promoted": None, "reason": "rien"}]
        report = make_run(outcomes, max_passes=5, stability_rounds=2).run([TASK])
        assert report.stable_at == 2

    def test_it_never_exceeds_the_pass_ceiling(self):
        outcomes = [{"promoted": f"e{i}", "reason": "toujours mieux"} for i in range(20)]
        report = make_run(outcomes, max_passes=3).run([TASK])
        assert len(report.passes) == 3
        assert report.stable_at is None
        assert "plafond" in report.reason

    def test_the_bounds_come_from_policy_not_hardcoded(self):
        assert "autonomy" in POLICY
        assert POLICY["autonomy"]["max_passes"] >= 1
        assert POLICY["autonomy"]["stability_rounds"] >= 1


class TestReporting:
    def test_the_final_choice_per_capability_is_reported(self):
        outcomes = [{"promoted": "a", "reason": "1"}, {"promoted": "b", "reason": "2"}]
        report = make_run(outcomes, max_passes=2).run([TASK])
        assert report.promotions == {"3d": {"engine": "b", "reason": "2"}}

    def test_a_blocked_capability_reports_why(self):
        outcomes = [{"promoted": None, "reason": "aucun adaptateur"}]
        report = make_run(outcomes).run([TASK])
        assert "aucun adaptateur" in report.blocked["3d"]

    def test_the_report_serialises(self):
        report = make_run([{"promoted": None, "reason": "rien"}]).run([TASK])
        data = report.to_dict()
        assert data["stable_at_pass"] == 1
        assert isinstance(data["passes"], list) and data["passes"][0]["index"] == 1

    def test_promotion_records_the_state_it_observed(self):
        report = make_run([{"promoted": "a", "reason": "x"}]).run([TASK])
        assert report.passes[0].to_dict()["promoted"]["3d"]["engine"] == "a"


class TestNoAsking:
    def test_research_candidate_without_a_runner_is_refused_by_name(self):
        """Better a clear refusal than a stack trace from an unimplemented adapter."""
        from aurora_cli.evolution import Candidate

        runner = make_run([{"promoted": None, "reason": "x"}])
        candidate = Candidate(name="unsupported", capability="3d", spec="unknown/unsupported-3d",
                              source="research", adapter={})
        with pytest.raises(RuntimeError) as excinfo:
            runner._attempt_for(TASK)(candidate)
        assert "no runner declares" in str(excinfo.value)
        assert "unsupported-3d" in str(excinfo.value)
        assert "adapters.toml" in str(excinfo.value)

    def test_a_candidate_with_a_runner_is_passed_to_the_probe(self, monkeypatch):
        import aurora_cli.capability_probe as probe
        from aurora_cli.evolution import Candidate

        seen = {}

        def fake_run(capability, candidate, fixture=None):
            seen["capability"] = capability
            seen["candidate"] = candidate.name
            return candidate

        monkeypatch.setattr(probe, "run_probe", fake_run)
        runner = make_run([{"promoted": None, "reason": "x"}])
        # The spec is what the manifest declares, so resolution succeeds.
        candidate = Candidate(name="hunyuan", capability="3d", spec="tencent/Hunyuan3D-2.1",
                              source="research", adapter={})
        runner._attempt_for(TASK)(candidate)
        assert seen == {"capability": "3d", "candidate": "hunyuan"}
        assert candidate.adapter["id"] == "hunyuan3d"
        assert candidate.adapter["env"]["PYTORCH_MPS_LOW_WATERMARK_RATIO"] == "1.0"


class TestEntryPoint:
    def test_it_builds_tasks_and_returns_a_report_without_input(self, monkeypatch):
        import aurora_cli.autonomous as autonomous

        runner = make_run([{"promoted": None, "reason": "rien"}])
        monkeypatch.setattr(autonomous, "AutonomousRun",
                            lambda **kwargs: runner)
        data = run_autonomous([{"capability": "3d"}])
        assert data["stable_at_pass"] == 1
        assert runner.loop.calls == 1
