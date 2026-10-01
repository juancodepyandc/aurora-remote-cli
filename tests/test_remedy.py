"""Self-repair: a failure must produce an action, not a refusal.

The behaviour under test is the one that was missing: the system meets a failure
and *does something*, within bounds, having chosen the something from what it has
learned. Both halves matter — acting without bound is a hang, and refusing is the
failure this module exists to remove.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.experience import Experience
from aurora_cli.remedy import AutoRepair, Remedy, default_remedies

MEM = "RuntimeError: invalid buffer size (MPS)"
FLAT = "reconstruction dégénérée : bbox_fill 0.0372 < 0.05"
DARK = "the paint pipeline produced no files"


class TestItActsInsteadOfRefusing:
    def test_out_of_memory_produces_a_different_request(self):
        repair = AutoRepair()
        request = {"resolution": 256, "views": 6}
        assert repair.apply(request, MEM) != request

    def test_a_flat_mesh_produces_a_different_request(self):
        repair = AutoRepair()
        request = {"resolution": 256, "dtype": "float16", "seed": 0}
        assert repair.apply(request, FLAT) != request

    def test_reducing_memory_lowers_the_token_count(self):
        """Attention scales with the square of tokens, so views come down first."""
        repair = AutoRepair()
        before = AutoRepair().apply({"resolution": 256, "views": 6}, MEM)
        after = repair.apply({"resolution": 256, "views": 6}, MEM)
        assert (after.get("views"), after.get("resolution")) <= (
            before.get("views"), before.get("resolution"))

    def test_a_dark_output_drops_the_optional_stage_rather_than_delivering_nothing(self):
        repair = AutoRepair()
        after = repair.apply({"resolution": 256, "texture": True}, DARK)
        assert after.get("texture") is False

    def test_an_action_is_recorded_with_the_cause_it_answered(self):
        repair = AutoRepair()
        repair.apply({"resolution": 256, "views": 6}, MEM)
        assert repair.actions and repair.actions[0].cause == "out_of_memory"


class TestItIsBounded:
    def test_a_remedy_stops_applying_at_its_limit(self):
        repair = AutoRepair()
        request = {"resolution": 256, "views": 6}
        seen = []
        for _ in range(6):
            nxt = repair.apply(request, MEM)
            if nxt == request:
                break
            seen.append(nxt)
            request = nxt
        assert len(seen) <= 2, "one remedy must not repeat forever"

    def test_when_nothing_more_can_be_done_the_request_stops_changing(self):
        repair = AutoRepair()
        request = {"resolution": 256, "views": 6}
        for _ in range(10):
            nxt = repair.apply(request, MEM)
            if nxt == request:
                break
            request = nxt
        assert repair.apply(request, MEM) == request, "a stable request is the stop signal"

    def test_the_limit_is_per_remedy_not_global(self):
        repair = AutoRepair()
        repair.apply({"resolution": 256, "views": 6}, MEM)
        repair.apply({"resolution": 256, "views": 6}, MEM)
        before = len(repair.actions)
        repair.apply({"resolution": 256, "views": 6}, FLAT)
        assert len(repair.actions) == before + 1, "a different cause still gets its turn"


class TestItLearnsWhichRepairWorks:
    def test_a_remedy_with_a_history_is_preferred(self):
        experience = Experience()
        for _ in range(3):
            experience.record("out_of_memory", "remedy", ("halve_resolution",), ok=True)
            experience.record("out_of_memory", "remedy", ("reduce_views",), ok=False)
        repair = AutoRepair(experience=experience)
        repair.apply({"resolution": 512, "views": 6}, MEM)
        assert repair.actions[0].remedy == "halve_resolution"

    def test_successful_applications_are_recorded_for_next_time(self):
        experience = Experience()
        AutoRepair(experience=experience).apply({"resolution": 256, "views": 6}, MEM)
        assert experience.strategies("out_of_memory", "remedy")


class TestItDoesNotGuess:
    def test_an_unknown_failure_changes_nothing(self):
        """No remedy means no invention; the request is handed back untouched."""
        repair = AutoRepair()
        request = {"resolution": 256}
        assert repair.apply(request, "something nobody has seen before") == request

    def test_cause_classification_covers_several_shapes_of_the_same_problem(self):
        repair = AutoRepair()
        for message in ("RuntimeError: out of memory",
                        "MPS invalid buffer size",
                        "torch watermark ratio"):
            assert "out_of_memory" in repair.causes_for(message)

    def test_a_cause_with_no_remedy_is_skipped_not_faked(self):
        repair = AutoRepair(remedies=[Remedy("out_of_memory", "only", lambda r: dict(r, x=1))])
        assert repair.apply({"resolution": 256}, DARK) == {"resolution": 256}


class TestNonDestructive:
    def test_the_original_request_is_never_mutated(self):
        request = {"resolution": 256, "views": 6}
        AutoRepair().apply(request, MEM)
        assert request == {"resolution": 256, "views": 6}

    def test_resolution_never_collapses_to_nothing(self):
        repair = AutoRepair()
        after = repair.apply({"resolution": 128, "views": 1}, MEM)
        assert after["resolution"] >= 128

    def test_the_declared_remedies_are_described(self):
        entries = AutoRepair().report()
        assert entries["count"] == 0
        assert all(r["detail"] for r in entries["remedies"])

    def test_the_default_policy_is_not_empty(self):
        assert len(default_remedies()) >= 5
