"""Discovery of model *directories*, not just weight files.

A 3D generator is a tree of checkpoints that only works as a set, so the
scanner has to report the set. These tests pin the shapes that actually exist
on a Hunyuan3D install: a Diffusers pipeline with ``model_index.json`` and a
single-stage checkpoint with just ``config.yaml``.
"""

from __future__ import annotations

import json

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
