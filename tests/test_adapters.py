"""The runner manifest: one declarative path from a capability to a command.

These tests treat the manifest as the contract. A new platform or model is
supposed to be a config edit, so anything that would force a code change — an
unresolvable capability, a missing placeholder, an environment that only exists in
Python — has to fail here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.adapters import (
    AdapterRegistry,
    manifest_path,
    load_manifest,
    placeholders,
)


@pytest.fixture
def registry():
    return AdapterRegistry()


class TestManifest:
    def test_the_shipped_manifest_is_readable(self):
        assert manifest_path().is_file()
        assert load_manifest()["runner"]

    def test_a_missing_manifest_is_an_error_not_an_empty_registry(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JOBIA_ADAPTERS", str(tmp_path / "absent.toml"))
        with pytest.raises(FileNotFoundError):
            AdapterRegistry()

    def test_every_runner_declares_what_it_can_prove(self, registry):
        for runner in registry.runners.values():
            assert runner.provides, f"{runner.id} must declare its checks"

    def test_every_runner_declares_its_capability_and_argv(self, registry):
        for runner in registry.runners.values():
            assert runner.capability and runner.argv


class TestResolution:
    def test_a_declared_spec_resolves(self, registry):
        runner, reason = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        assert runner is not None and runner.id == "hunyuan3d"

    def test_resolution_is_case_insensitive(self, registry):
        assert registry.resolve("3d", "TENCENT/HUNYUAN3D-2.1")[0] is not None

    def test_an_undeclared_model_is_refused_by_name(self, registry):
        runner, reason = registry.resolve("3d", "unknown/unsupported-3d")
        assert runner is None
        assert "no runner declares" in reason and "unsupported-3d" in reason

    def test_the_refusal_says_where_to_declare_it(self, registry):
        assert "adapters.toml" in registry.resolve("3d", "unknown/model")[1]

    def test_an_entirely_unknown_capability_is_refused_too(self, registry):
        runner, reason = registry.resolve("telepathy", "whatever")
        assert runner is None and "no runner declares" in reason


class TestCommandBuilding:
    def test_placeholders_become_single_arguments(self, registry):
        runner, _ = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        command = registry.build(runner, interpreter="py", worker="w.py", root="/r",
                                 repo="/r/h", image="i.png", output="o.glb", model="m",
                                 octree=384, steps=50, min_bbox_fill=0.05, paint_flag="")
        assert command[0] == "py" and "w.py" in command
        assert "--paint" not in command

    def test_an_empty_placeholder_disappears_cleanly(self, registry):
        runner, _ = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        command = registry.build(runner, interpreter="py", worker="w.py", root="/r",
                                 repo="/r/h", image="i.png", output="o.glb", model="m",
                                 octree=384, steps=50, min_bbox_fill=0.05, paint_flag="")
        assert "" not in command

    def test_a_path_with_spaces_cannot_inject_arguments(self, registry):
        """A single placeholder is one argv entry, whatever it contains."""
        runner, _ = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        command = registry.build(runner, interpreter="py", worker="w.py",
                                 root="/r --evil", repo="/r/h", image="i.png",
                                 output="o.glb", model="m", octree=384, steps=50,
                                 min_bbox_fill=0.05, paint_flag="")
        assert "--evil" not in command
        assert "/r --evil" in command

    def test_a_missing_placeholder_is_refused_by_name(self, registry):
        runner, _ = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        with pytest.raises(KeyError) as excinfo:
            registry.build(runner, interpreter="py")
        assert "root" in str(excinfo.value)

    def test_the_worker_path_is_required_so_nothing_guesses_it(self, registry):
        runner, _ = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        assert "worker" in runner.required


class TestEnvironment:
    def test_the_mps_watermarks_are_declared_in_the_manifest(self, registry):
        """They were rediscovered by crashing; now they are data."""
        runner, _ = registry.resolve("3d", "tencent/Hunyuan3D-2.1")
        env = runner.environment()
        assert env["PYTORCH_MPS_LOW_WATERMARK_RATIO"] == "1.0"
        assert env["PYTORCH_MPS_HIGH_WATERMARK_RATIO"] == "1.0"

    def test_environment_entries_are_all_pairs(self, registry):
        for runner in registry.runners.values():
            assert set(runner.environment()) <= {
                "PYTORCH_ENABLE_MPS_FALLBACK",
                "PYTORCH_MPS_HIGH_WATERMARK_RATIO",
                "PYTORCH_MPS_LOW_WATERMARK_RATIO",
            }


class TestGaps:
    def test_missing_runners_are_reported_per_spec(self, registry):
        gaps = registry.gaps("3d", ["tencent/Hunyuan3D-2.1", "unknown/unsupported-3d"])
        assert "unknown/unsupported-3d" in gaps
        assert "tencent/Hunyuan3D-2.1" not in gaps

    def test_reporting_a_gap_is_not_the_same_as_failing_the_capability(self, registry):
        """The registry answers a question; it does not decide quality."""
        assert registry.gaps("3d", ["unknown/unsupported-3d"])
        assert registry.resolve("3d", "tencent/Hunyuan3D-2.1")[0] is not None
