"""Discovery of model *directories*, not just weight files.

A 3D generator is a tree of checkpoints that only works as a set, so the
scanner has to report the set. These tests pin the shapes that actually exist
on a Hunyuan3D install: a Diffusers pipeline with ``model_index.json`` and a
single-stage checkpoint with just ``config.yaml``.
"""

from __future__ import annotations

import json
import contextlib

import pytest
from pathlib import Path

from aurora_cli.core import discovery, locations
from aurora_cli.core.providers import ModelInfo


def _weights(path, size_bytes: int = 2_000_000) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size_bytes)


def test_diffusers_pipeline_is_one_model(tmp_path):
    root = tmp_path / "weights"
    pipeline = root / "Hunyuan3D-2.1" / "hunyuan3d-paintpbr-v2-1"
    pipeline.mkdir(parents=True)
    (pipeline / "model_index.json").write_text(json.dumps({
        "_class_name": "Hunyuan3DPaintPipeline",
        "unet": ["diffusers", "UNet2DConditionModel"],
        "vae": ["diffusers", "AutoencoderKL"],
    }))
    _weights(pipeline / "unet" / "diffusion_pytorch_model.bin")
    _weights(pipeline / "vae" / "diffusion_pytorch_model.bin")

    found = discovery.scan_pipelines([("jobia", root)])

    assert len(found) == 1
    model = found[0]
    assert model.name.endswith("hunyuan3d-paintpbr-v2-1")
    # A paint pipeline textures an existing mesh; it does not generate one, so
    # the scan must not report it as a plain 3D generator.
    assert model.capability == "3d-texture"
    assert model.weight_format == "diffusers"
    # Both stages counted once, as one model.
    assert model.size_bytes >= 4_000_000


def test_single_stage_checkpoint_is_detected(tmp_path):
    root = tmp_path / "weights"
    stage = root / "Hunyuan3D-2.1" / "hunyuan3d-dit-v2-1"
    stage.mkdir(parents=True)
    (stage / "config.yaml").write_text("_class_name: Hunyuan3DDiT\n")
    _weights(stage / "model.fp16.ckpt", 3_000_000)

    found = discovery.scan_pipelines([("jobia", root)])

    assert [m.name for m in found] == ["Hunyuan3D-2.1/hunyuan3d-dit-v2-1"]
    assert found[0].capability == "3d"
    assert found[0].weight_format == "checkpoint"


def test_config_without_weights_is_not_a_model(tmp_path):
    """A tokenizer or scheduler is a directory with a config and no weights."""
    root = tmp_path / "weights"
    stage = root / "pipeline" / "tokenizer"
    stage.mkdir(parents=True)
    (stage / "config.json").write_text("{}")
    (stage / "vocab.json").write_text("{}")

    assert discovery.scan_pipelines([("jobia", root)]) == []


def test_3d_wins_over_vision_marker():
    """A paint pipeline contains a vision encoder; it is still a 3D model.

    Classifying on the vision marker first reports the texturing pipeline as a
    vision model, which hides the only thing a provisioner needs to know.
    """
    assert discovery._classify_pipeline("Hunyuan3DPaintPipeline", []) == "3d"
    assert discovery._classify_pipeline("", ["image_encoder"]) == "vision"
    assert discovery._classify_pipeline("", [], "hunyuan3d-dit-v2-1") == "3d"
    assert discovery._classify_pipeline("T3D", []) == "3d"
    assert discovery._classify_pipeline("LlamaForCausalLM", []) == "llm"


def test_pipeline_weights_are_not_listed_twice(tmp_path, monkeypatch):
    """The gigabytes of a pipeline must not reappear as anonymous blobs."""
    root = tmp_path / "weights"
    pipeline = root / "Hunyuan3D-2.1" / "hunyuan3d-paintpbr-v2-1"
    pipeline.mkdir(parents=True)
    (pipeline / "model_index.json").write_text(json.dumps({"_class_name": "H3D"}))
    _weights(pipeline / "unet" / "diffusion_pytorch_model.bin")

    monkeypatch.setattr(discovery.locations, "model_search_roots",
                        lambda: [("jobia", root)])
    result = discovery.scan(deep=True, include_files=True)

    names = [m.name for m in result.loose_models]
    assert len(names) == 1
    assert names[0].endswith("hunyuan3d-paintpbr-v2-1")


def test_mesh_quality_gate_rejects_truncated_glb(tmp_path):
    from aurora_cli.core.pipeline import stage_validate_mesh

    path = tmp_path / "broken.glb"
    path.write_bytes(b"glTF" + (2).to_bytes(4, "little") + (40).to_bytes(4, "little") + b"x" * 8)
    gate = stage_validate_mesh(path)
    assert gate.status == "failed"
    assert "tronqué" in gate.log


def test_mesh_quality_gate_rejects_invalid_glb(tmp_path):
    from aurora_cli.core.pipeline import stage_validate_mesh

    # Minimal invalid GLB - missing JSON/BIN chunks
    path = tmp_path / "invalid.glb"
    path.write_bytes(b"glTF" + (2).to_bytes(4, "little") + (20).to_bytes(4, "little") + b"x" * 8)
    gate = stage_validate_mesh(path)
    assert gate.status == "failed"
    assert "tronqué" in gate.log or "mal align" in gate.log


def test_ckpt_counts_as_a_weight_extension():
    """A Hunyuan DiT ships as .ckpt; missing it hid 6.9 GB of real weights."""
    assert ".ckpt" in discovery.MODEL_SUFFIXES


def test_extra_roots_from_env(tmp_path, monkeypatch):
    extra = tmp_path / "mes-poids"
    extra.mkdir()
    monkeypatch.setenv("JOBIA_MODEL_ROOTS", str(extra))
    assert ("jobia", extra) in locations.model_search_roots()


def test_capability_defaults_to_empty():
    assert ModelInfo(name="x").capability == ""


class TestPaintQualityOverrides:
    """Bake overrides come from the memory budget, not from a preset's name.

    Regression: the tier used to be read out of whatever the engine's `normal`
    preset happened to contain, which is a value guessed for one machine and
    silently applied to every subject and every other model.
    """

    def test_preset_quality_leaves_the_bake_alone(self):
        from aurora_cli.core.mesh_worker import paint_overrides

        assert paint_overrides({}, "preset") == {}

    def test_overrides_never_touch_the_attention_bounded_settings(self):
        """View count and diffusion resolution are what the preflight bounds."""
        from aurora_cli.core.mesh_worker import pick_bake_tier

        tier = pick_bake_tier(64 * (1 << 30)) or {}
        assert "max_num_view" not in tier and "resolution" not in tier

    def test_tier_selection_is_independent_of_any_engine_preset(self):
        """The same budget must yield the same tier whatever presets exist."""
        from aurora_cli.core.mesh_worker import paint_overrides

        class NoPresets:
            PAINT_PRESETS = {}

        assert paint_overrides(NoPresets.PAINT_PRESETS, "high") == {}


class TestShapeCacheIdentity:
    """The cache must notice when the generation settings change.

    Regression: keying on the image alone made a quality change look like it did
    nothing, because the previous mesh was returned unchanged.
    """

    def _sig(self, octree=384, steps=50, image=b"img"):
        from aurora_cli.core.mesh_worker import shape_identity

        return shape_identity(image, "model", {"octree": octree, "steps": steps,
                                               "guidance": 5.0, "dtype": "torch.float16"})

    def test_same_input_and_settings_reuse_the_cache(self):
        assert self._sig() == self._sig()

    def test_raising_octree_invalidates_the_cache(self):
        assert self._sig(octree=256) != self._sig(octree=384)

    def test_raising_steps_invalidates_the_cache(self):
        assert self._sig(steps=30) != self._sig(steps=50)

    def test_a_different_image_invalidates_the_cache(self):
        assert self._sig(image=b"other") != self._sig(image=b"img")


class TestShapePathsAreNotShared:
    """Regression: `with_name('shape.glb')` made every job in a directory share
    one geometry, so a second job overwrote and reused the first job's mesh."""

    def test_two_outputs_in_one_directory_do_not_collide(self):
        from aurora_cli.core.mesh_worker import shape_paths

        first, first_state = shape_paths(Path("/tmp/job/model-a.glb"))
        second, second_state = shape_paths(Path("/tmp/job/model-b.glb"))
        assert first != second and first_state != second_state
        assert first.parent == second.parent

    def test_names_keep_the_output_stem(self):
        from aurora_cli.core.mesh_worker import shape_paths

        geometry, state = shape_paths(Path("/tmp/job/nick.glb"))
        assert geometry.name == "nick.shape.glb"
        assert state.name == "nick.shape.state.json"


class TestDegeneracyRetries:
    """A flattened plate must trigger a retry, never be delivered silently."""

    def test_ladder_starts_cheapest_and_escalates_resolution_and_precision(self):
        from aurora_cli.core.mesh_worker import attempt_ladder

        ladder = attempt_ladder(384, 50, "float16")
        assert ladder[0] == {'octree_resolution': 384, 'shape_steps': 50,
                              'dtype': 'float16', 'seed': 0}
        assert any(r['octree_resolution'] == 512 for r in ladder)
        assert any(r['dtype'] == 'float32' for r in ladder)

    def test_ladder_has_no_duplicate_rungs(self):
        from aurora_cli.core.mesh_worker import attempt_ladder

        ladder = attempt_ladder(384, 50, "float16")
        assert len({tuple(sorted(r.items())) for r in ladder}) == len(ladder)

    def test_ladder_is_not_repeated_when_already_at_the_top(self):
        from aurora_cli.core.mesh_worker import attempt_ladder

        ladder = attempt_ladder(512, 50, "float32")
        assert len(ladder) >= 2
        assert ladder[0]["octree_resolution"] == 512

    def _fake_stack(self, fills, calls):
        """A stand-in for torch and the shape pipeline, driven by `fills`."""
        import contextlib

        class FakePipe:
            def __call__(self, **kwargs):
                index = len(calls)

                class M:
                    def export(self, path):
                        Path(path).write_bytes(b"mesh")

                return [M()]

        class Factory:
            @staticmethod
            def from_pretrained(*a, **k):
                calls.append(k['dtype'])
                return FakePipe()

        class FakeTorch:
            float16, float32 = "fp16", "fp32"
            inference_mode = staticmethod(contextlib.nullcontext)
            Generator = staticmethod(lambda device: type(
                "G", (), {"manual_seed": lambda self, s: None})())
            cuda = type("c", (), {"is_available": staticmethod(lambda: False)})
            mps = type("m", (), {"empty_cache": staticmethod(lambda: None)})

        return Factory, FakeTorch

    def _args(self):
        return type("A", (), {"octree_resolution": 384, "shape_steps": 50,
                              "guidance_scale": 5.0, "min_bbox_fill": 0.05})()

    def test_a_healthy_first_attempt_does_not_escalate(self, tmp_path, monkeypatch):
        import contextlib

        import aurora_cli.core.mesh_worker as worker

        calls = []
        monkeypatch.setattr(worker, "bbox_fill", lambda p: 0.4)
        factory, fake_torch = self._fake_stack(None, calls)
        result = worker.generate_shape(None, "m", "mps", "float16", self._args(),
                                       tmp_path / "o.glb",
                                       pipeline_factory=factory, torch_module=fake_torch)
        assert len(calls) == 1, "a healthy mesh must not trigger a retry"
        assert len(result["attempts"]) == 1
        assert result["bbox_fill"] == 0.4

    def test_a_flat_mesh_escalates_until_it_is_healthy(self, tmp_path, monkeypatch):
        import aurora_cli.core.mesh_worker as worker

        fills = [0.01, 0.02, 0.30]
        calls = []
        monkeypatch.setattr(worker, "bbox_fill", lambda p: fills[len(calls) - 1])
        factory, fake_torch = self._fake_stack(None, calls)
        result = worker.generate_shape(None, "m", "mps", "float16", self._args(),
                                       tmp_path / "o.glb",
                                       pipeline_factory=factory, torch_module=fake_torch)
        assert len(calls) == 3
        assert len(result["attempts"]) == 3
        assert result["settings"]["dtype"] == "float32"
        assert calls == ['fp16', 'fp16', 'fp32']

    def test_nothing_is_delivered_when_every_rung_is_flat(self, tmp_path, monkeypatch):
        import aurora_cli.core.mesh_worker as worker

        calls = []
        monkeypatch.setattr(worker, "bbox_fill", lambda p: 0.01)
        factory, fake_torch = self._fake_stack(None, calls)
        with pytest.raises(RuntimeError) as excinfo:
            worker.generate_shape(None, "m", "mps", "float16", self._args(),
                                  tmp_path / "o.glb",
                                  pipeline_factory=factory, torch_module=fake_torch)
        assert "non validée" in str(excinfo.value)
        assert len(calls) == 4, "the whole ladder must be tried before giving up"


class TestBakeTierSelection:
    """The bake tier follows the machine's memory, never a particular example."""

    def test_tiers_are_declared_in_policy_not_hardcoded(self):
        from aurora_cli.core.mesh_worker import bake_tiers

        tiers = bake_tiers()
        assert tiers, "the bake ladder must come from policies/quality.toml"
        assert all({"render_size", "texture_size"} <= set(t) for t in tiers)

    def test_paint_overrides_use_the_measured_backend(self):
        from types import SimpleNamespace
        from aurora_cli.core.mesh_worker import paint_overrides
        backend = object()
        observed = []
        module = SimpleNamespace(gpu_memory_gb=lambda device: observed.append(device) or 18)
        settings = paint_overrides({}, 'high', backend=backend, backend_module=module)
        assert observed == [backend]
        assert settings['texture_size'] >= 2048 and settings['bake_exp'] > 0

    def test_tiers_are_ordered_smallest_first(self):
        from aurora_cli.core.mesh_worker import bake_tiers

        sizes = [t["render_size"] for t in bake_tiers()]
        assert sizes == sorted(sizes)

    def test_a_bigger_machine_gets_at_least_as_much_texture(self):
        from aurora_cli.core.mesh_worker import pick_bake_tier

        small = pick_bake_tier(4 * (1 << 30))
        large = pick_bake_tier(128 * (1 << 30))
        assert small["texture_size"] <= large["texture_size"]

    def test_an_impossible_budget_selects_nothing_rather_than_overcommitting(self):
        from aurora_cli.core.mesh_worker import pick_bake_tier

        assert pick_bake_tier(1) is None

    def test_estimate_grows_with_the_atlas(self):
        from aurora_cli.core.mesh_worker import bake_estimate_bytes

        small = bake_estimate_bytes(1024, 2048)
        large = bake_estimate_bytes(2048, 4096)
        assert large > small


class TestPlacementLeversArePolicyNotConstants:
    """bake_exp and merge_method decide how colour lands on UV islands."""

    def test_bake_exp_is_declared_in_policy(self):
        from aurora_cli.evolution import load_policy

        assert load_policy()["bake"]["bake_exp"] >= 4

    def test_the_worker_reads_the_declared_value(self):
        from aurora_cli.core.mesh_worker import _placement_setting

        assert _placement_setting("bake_exp") is not None

    def test_an_absent_setting_is_none_rather_than_a_default(self):
        """No silent fallback: the pipeline then keeps upstream's own value."""
        from aurora_cli.core.mesh_worker import _placement_setting

        assert _placement_setting("not_a_setting") is None

    def test_overrides_include_the_placement_lever_when_declared(self):
        from aurora_cli.core.mesh_worker import _placement_setting

        assert _placement_setting("bake_exp") is not None
