"""Research: candidate discovery, refusals and failure explanation.

Every test uses `StubHubClient`, so the suite never touches the network and a
Hub outage cannot turn into a red build.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.evolution import TaskSpec
from aurora_cli.research import (
    HuggingFaceResearcher,
    StubHubClient,
    load_research_policy,
    research_policy_path,
)

GIB = 1 << 30
TASK = TaskSpec(capability="3d")

CATALOGUE = {
    "good/model-small": {
        "filter": "image-to-3d", "downloads": 5000, "likes": 100,
        "size_bytes": 8 * GIB, "files": ["config.json", "model.safetensors"],
    },
    "vendor/hunyuan3d-2.1": {
        "filter": "image-to-3d", "downloads": 9000, "likes": 300,
        "size_bytes": 20 * GIB, "files": ["hunyuan3d-dit-v2-1/model.fp16.ckpt"],
    },
    "vendor/Lora-finetune": {
        "filter": "image-to-3d", "downloads": 4000, "likes": 20,
        "size_bytes": 4 * GIB, "files": ["adapter_model.safetensors"],
    },
    "vendor/huge-model": {
        "filter": "image-to-3d", "downloads": 3000, "likes": 50,
        "size_bytes": 90 * GIB, "files": ["model.safetensors"],
    },
    "vendor/no-weights": {
        "filter": "image-to-3d", "downloads": 2000, "likes": 10,
        "size_bytes": 1 * GIB, "files": ["README.md", "config.json"],
    },
    "vendor/unknown-size": {
        "filter": "image-to-3d", "downloads": 2500, "likes": 15,
        "size_bytes": None, "files": ["model.safetensors"],
    },
}


@pytest.fixture
def policy():
    return load_research_policy()


def make(policy, free=500 * GIB):
    return HuggingFaceResearcher(StubHubClient(CATALOGUE), policy=policy,
                                 free_space_probe=lambda p: free)


class TestPolicy:
    def test_packaged_profile_is_readable(self):
        assert research_policy_path().is_file()
        assert "3d" in load_research_policy()["capabilities"]

    def test_unknown_capability_is_refused_not_guessed(self, policy):
        with pytest.raises(KeyError):
            make(policy).profile("telepathy")


class TestDiscovery:
    def test_proposes_runnable_candidates(self, policy):
        specs = {c.spec for c in make(policy).candidates(TASK, limit=10)}
        assert "good/model-small" in specs

    def test_excluded_pattern_is_refused_with_a_reason(self, policy):
        researcher = make(policy)
        specs = {c.spec for c in researcher.candidates(TASK, limit=10)}
        assert "vendor/Lora-finetune" not in specs
        assert any("excluded by pattern" in n for n in researcher.notes)

    def test_oversized_model_is_refused(self, policy):
        researcher = make(policy)
        specs = {c.spec for c in researcher.candidates(TASK, limit=10)}
        assert "vendor/huge-model" not in specs
        assert any("policy ceiling" in n for n in researcher.notes)

    def test_unknown_size_is_refused(self, policy):
        researcher = make(policy)
        specs = {c.spec for c in researcher.candidates(TASK, limit=10)}
        assert "vendor/unknown-size" not in specs
        assert any("cannot be bounded" in n for n in researcher.notes)

    def test_model_without_weights_is_refused(self, policy):
        researcher = make(policy)
        specs = {c.spec for c in researcher.candidates(TASK, limit=10)}
        assert "vendor/no-weights" not in specs
        assert any("no weight file" in n for n in researcher.notes)

    def test_free_space_bounds_the_search(self, policy):
        tight = make(policy, free=2 * GIB)
        specs = {c.spec for c in tight.candidates(TASK, limit=10)}
        assert "good/model-small" not in specs
        assert any("free space" in n for n in tight.notes)

    def test_filter_comes_from_the_profile_not_the_code(self, policy):
        client = StubHubClient(CATALOGUE)
        HuggingFaceResearcher(client, policy=policy).candidates(TASK, limit=3)
        assert client.calls[0]["filter"] == policy["capabilities"]["3d"]["filter"]


class TestAdapters:
    def test_vendor_keyword_does_not_imply_an_executable_architecture(self, policy):
        found = {c.spec: c for c in make(policy).candidates(TASK, limit=10)}
        assert found["vendor/hunyuan3d-2.1"].adapter == {}

    def test_unknown_model_gets_an_empty_adapter_not_a_guess(self, policy):
        found = {c.spec: c for c in make(policy).candidates(TASK, limit=10)}
        assert found["good/model-small"].adapter == {}


class TestExplain:
    def test_known_failure_is_diagnosed_from_the_profile(self, policy):
        note = make(policy).explain("RuntimeError: invalid low watermark ratio 1.4", TASK)
        assert "known issue" in note and "PYTORCH_MPS_LOW_WATERMARK_RATIO" in note

    def test_unknown_failure_says_so_instead_of_guessing(self, policy):
        note = make(policy).explain("RuntimeError: something never seen before", TASK)
        assert "not a known issue" in note

    def test_failure_on_unknown_capability_still_answers(self, policy):
        note = make(policy).explain("boom", TaskSpec(capability="telepathy"))
        assert "no research profile" in note
