"""The queue must never stop early, and must always justify itself.

The failure these tests guard against is subtle: a system that reports "nothing
to do" while measurements show unresolved gaps. Everything else is bookkeeping.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.adapters import AdapterRegistry
from aurora_cli.experience import Experience
from aurora_cli.platforms import Host, Resolver
from aurora_cli.queue import Queue, Task, build, from_adapters, from_experience, from_platforms, load, persist


class TestItAlwaysHasSomething:
    def test_a_gap_produces_a_task(self):
        queue = from_adapters(AdapterRegistry(), {"3d": ["unknown/unsupported-3d"]})
        assert queue.next_task() is not None

    def test_the_task_carries_the_evidence_that_produced_it(self):
        queue = from_adapters(AdapterRegistry(), {"3d": ["unknown/unsupported-3d"]})
        assert "unsupported-3d" in queue.next_task().evidence

    def test_no_gap_means_no_work_rather_than_invented_work(self):
        """Fabricating a task would be worse than an honest empty queue."""
        assert from_adapters(AdapterRegistry(), {"3d": ["tencent/Hunyuan3D-2.1"]}).next_task() is None

    def test_next_task_is_the_highest_priority_one(self):
        queue = Queue([Task("b", "x", "second", "e", priority=80),
                       Task("a", "x", "first", "e", priority=10)])
        assert queue.next_task().id == "a"

    def test_traffic_decides_between_capabilities(self):
        """A capability nobody uses is not the same as one under load."""
        specs = {"3d": ["unknown/unsupported-3d"], "image": ["unknown/image-model"]}
        idle = from_adapters(AdapterRegistry(), specs)
        busy = from_adapters(AdapterRegistry(), specs, traffic={"3d": 10})
        assert busy.sorted()[0].id == "adapter:3d"
        assert idle.sorted()[0].id == "adapter:3d"


class TestPlatformGaps:
    def test_an_unresolvable_platform_becomes_work(self):
        resolver = Resolver({"target": {"only-mac": {"system": "darwin", "package": {}}}})
        host = Host("plan9", "risc", "risc", (3, 11), "CPython", "", "cpu")
        queue = from_platforms(resolver, {"plan9": host})
        assert queue.next_task() and "plan9" in queue.next_task().id

    def test_an_unusable_package_becomes_work_with_the_package_named(self):
        resolver = Resolver()
        host = Host("linux", "x86_64", "x86_64", (3, 11), "CPython", "musl", "cpu")
        queue = from_platforms(resolver, {"alpine": host})
        assert "torch" in queue.next_task().evidence


class TestExperienceGaps:
    def test_an_excluded_configuration_becomes_a_retry_task(self):
        store = Experience()
        for _ in range(3):
            store.record("3d", "h", ("octree", 256), ok=False, detail="out of memory")
        queue = from_experience(store, "3d", "h")
        assert queue.next_task().kind == "remeasure"
        assert "octree" in queue.next_task().evidence

    def test_a_healthy_history_produces_no_work(self):
        store = Experience()
        store.record("3d", "h", ("octree", 512), ok=True)
        assert from_experience(store, "3d", "h").next_task() is None


class TestMerge:
    def test_sources_merge_without_cancelling_each_other(self):
        store = Experience()
        for _ in range(3):
            store.record("3d", "h", ("x",), ok=False, detail="timeout")
        queue = build(adapters=AdapterRegistry(),
                      capabilities={"3d": ["unknown/unsupported-3d"]},
                      experience=store, capability="3d", spec="h")
        kinds = {t.kind for t in queue.tasks}
        assert {"adapter", "remeasure"} <= kinds

    def test_identifiers_are_deduplicated(self):
        queue = Queue()
        queue.add(Task("same", "adapter", "one", "e"))
        queue.add(Task("same", "adapter", "two", "e"))
        assert len(queue.tasks) == 1


class TestPersistence:
    def test_the_queue_survives_so_the_next_run_continues(self, tmp_path):
        path = tmp_path / "queue.json"
        queue = from_adapters(AdapterRegistry(), {"3d": ["unknown/unsupported-3d"]})
        persist(queue, path)
        assert load(path).next_task().id == queue.next_task().id

    def test_a_missing_queue_loads_empty_rather_than_crashing(self, tmp_path):
        assert load(tmp_path / "absent.json").next_task() is None

    def test_a_corrupt_queue_loads_empty(self, tmp_path):
        path = tmp_path / "queue.json"
        path.write_text("{ broken", encoding="utf-8")
        assert load(path).next_task() is None

    def test_blocking_is_reported(self):
        queue = Queue([Task("a", "adapter", "t", "e", blocks=("m1", "m2"))])
        assert len(queue.blocked()) == 1
