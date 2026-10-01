"""Learning from outcomes: the proposal must actually change.

The claim being tested is narrow and checkable — after the same failure twice,
the system stops proposing that configuration, and after a success it puts that
configuration first. A memory that does not change behaviour is just a log.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.experience import Attempt, Experience, Strategy

FAST = ("octree", 512, "fp32", 1)
SLOW = ("octree", 384, "fp16", 0)
THIN = ("octree", 256, "fp16", 0)


@pytest.fixture
def store():
    return Experience()


class TestRecording:
    def test_a_failure_is_classified_by_the_store(self, store):
        attempt = store.record("3d", "hunyuan", FAST, ok=False,
                               detail="ModuleNotFoundError: No module named 'bpy'")
        assert attempt.cause == "missing_dependency"

    def test_a_success_records_no_cause(self, store):
        assert store.record("3d", "hunyuan", FAST, ok=True).cause == ""

    def test_strategies_count_attempts_and_successes(self, store):
        store.record("3d", "h", FAST, ok=True)
        store.record("3d", "h", FAST, ok=False, detail="out of memory")
        strategy = store.strategies("3d", "h")[0]
        assert strategy.tried == 2 and strategy.succeeded == 1

    def test_attempts_for_other_capabilities_do_not_leak(self, store):
        store.record("3d", "h", FAST, ok=True)
        assert store.strategies("image", "h") == []


class TestRanking:
    def test_an_unseen_configuration_is_neutral_not_a_failure(self, store):
        assert Strategy(config=THIN).success_rate == 0.5

    def test_a_configuration_that_worked_is_ranked_first(self, store):
        store.record("3d", "h", SLOW, ok=False, detail="bad output")
        store.record("3d", "h", FAST, ok=True)
        assert store.strategies("3d", "h")[0].config == FAST

    def test_order_puts_the_proven_configuration_first(self, store):
        store.record("3d", "h", SLOW, ok=False, detail="bad output")
        store.record("3d", "h", FAST, ok=True)
        assert store.order("3d", "h", [SLOW, FAST])[0] == FAST

    def test_order_keeps_every_candidate(self, store):
        ordered = store.order("3d", "h", [SLOW, FAST, THIN])
        assert sorted(ordered) == sorted([SLOW, FAST, THIN])


class TestExclusion:
    def _fail(self, store, times=3, config=THIN, detail="out of memory"):
        for _ in range(times):
            store.record("3d", "h", config, ok=False, detail=detail)

    def test_one_or_two_failures_are_not_enough_to_exclude(self, store):
        """Below the measured threshold, re-trying is still diligence."""
        self._fail(store, times=1)
        assert store.excluded("3d", "h") == set()
        self._fail(store, times=1)  # two in total
        assert store.excluded("3d", "h") == set()

    def test_three_identical_failures_exclude_the_configuration(self, store):
        self._fail(store, times=3)
        assert THIN in store.excluded("3d", "h")

    def test_a_single_success_rescues_a_configuration(self, store):
        self._fail(store, times=3)
        store.record("3d", "h", THIN, ok=True)
        assert THIN not in store.excluded("3d", "h")

    def test_excluded_configurations_go_last_but_are_not_deleted(self, store):
        self._fail(store, times=3)
        ordered = store.order("3d", "h", [THIN, FAST])
        assert ordered[-1] == THIN, "excluded means deprioritised, not discarded"
        assert len(ordered) == 2

    def test_different_specs_have_independent_histories(self, store):
        self._fail(store, times=3)
        assert store.excluded("3d", "other") == set()


class TestReflection:
    def test_it_states_what_learned_in_words(self, store):
        store.record("3d", "h", FAST, ok=True)
        for _ in range(3):
            store.record("3d", "h", THIN, ok=False, detail="out of memory")
        lessons = " ".join(store.reflect("3d", "h")["lessons"])
        assert "never worked" in lessons
        assert "excluded" in lessons

    def test_it_names_the_preferred_configuration(self, store):
        store.record("3d", "h", SLOW, ok=False, detail="bad output")
        store.record("3d", "h", FAST, ok=True)
        assert store.reflect("3d", "h")["preferred"] == list(FAST)

    def test_an_empty_history_reflects_without_claiming_anything(self, store):
        report = store.reflect("3d", "nothing")
        assert report["lessons"] == [] and report["preferred"] is None


class TestPersistence:
    def test_history_survives_a_restart(self, tmp_path):
        path = tmp_path / "experience.json"
        first = Experience(path)
        first.record("3d", "h", FAST, ok=True)
        for _ in range(3):
            first.record("3d", "h", THIN, ok=False, detail="out of memory")
        first.save()

        second = Experience(path)
        assert THIN in second.excluded("3d", "h"), "the lesson must outlive the process"
        assert second.order("3d", "h", [THIN, FAST])[-1] == THIN

    def test_a_corrupt_history_is_ignored_rather_than_crashing(self, tmp_path):
        path = tmp_path / "experience.json"
        path.write_text("{ not json", encoding="utf-8")
        assert Experience(path).attempts == []

    def test_saving_without_a_path_is_refused(self, store):
        with pytest.raises(ValueError):
            store.save()
