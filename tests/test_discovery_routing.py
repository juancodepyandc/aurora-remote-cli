"""Machine discovery, metadata parsing, and routing policy."""
from __future__ import annotations

from pathlib import Path

import pytest

from aurora_cli.core import discovery
from aurora_cli.core.providers import (
    LocalFlavour,
    ModelInfo,
    ProviderInfo,
    ProviderKind,
    parse_parameter_count,
    parse_quantization,
)
from aurora_cli.core.router import Mode, Router


@pytest.mark.parametrize("name,expected", [
    ("qwen3-30b", 30.0),
    ("llama3.2:3b", 3.0),
    ("gpt-oss:20b", 20.0),
    ("3.0B", 3.0),          # what Ollama returns in details
    ("7.6B", 7.6),
    ("0.5b", 0.5),
    ("llama-3-8B-Instruct", 8.0),
    ("phi3-mini:3.8b", 3.8),
    ("no-size-here", 0.0),
    ("", 0.0),
])
def test_parameter_parsing(name, expected):
    assert parse_parameter_count(name) == pytest.approx(expected)


@pytest.mark.parametrize("name,expected", [
    ("model-Q4_K_M.gguf", "Q4_K_M"),
    ("llama3:8b-instruct-q8_0", "Q8_0"),
    ("plain-model", ""),
])
def test_quantization_parsing(name, expected):
    assert parse_quantization(name) == expected


def test_scan_finds_gguf_files(tmp_path):
    """A weights file on disk must be reported even with no server running."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    # Above MIN_MODEL_BYTES, otherwise it is treated as a stray download.
    (models_dir / "llama-3-8B-Instruct-Q4_K_M.gguf").write_bytes(
        b"\0" * (discovery.MIN_MODEL_BYTES + 1))
    (models_dir / "notes.txt").write_text("ignore me")

    found, roots, files = discovery.scan_model_files(
        roots=[("test", models_dir)], max_files=100
    )
    names = [m.name for m in found]
    assert any("llama-3-8B" in n for n in names)
    # Only model weights count, not arbitrary files in the same directory.
    assert not any("notes" in n for n in names)
    assert roots == 1


def test_small_files_are_ignored(tmp_path):
    """A truncated download must not be reported as a usable model."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "interrupted-7b-q4.gguf").write_bytes(b"\0" * 512)
    found, _, _ = discovery.scan_model_files(roots=[("t", models_dir)], max_files=100)
    assert found == []


def test_symlinked_weights_are_found(tmp_path):
    """Hugging Face stores weights as symlinks into blobs/; those must count.

    If this regresses, every Hugging Face model looks absent.
    """
    hub = tmp_path / "hub"
    blobs = hub / "models--acme--model" / "blobs"
    snapshot = hub / "models--acme--model" / "snapshots" / "abc123"
    blobs.mkdir(parents=True)
    snapshot.mkdir(parents=True)
    payload = blobs / "0123abcd"
    payload.write_bytes(b"\0" * (discovery.MIN_MODEL_BYTES + 1))
    (snapshot / "model.safetensors").symlink_to(payload)

    found, _, _ = discovery.scan_model_files(roots=[("huggingface", hub)], max_files=100)
    assert found, "a symlinked weights file must be discovered"
    assert found[0].size_bytes > discovery.MIN_MODEL_BYTES


def test_symlink_loop_does_not_hang(tmp_path):
    """A directory linking to its own ancestor must terminate."""
    root = tmp_path / "loop"
    root.mkdir()
    (root / "weights").mkdir()
    (root / "weights" / "model-q4.gguf").write_bytes(
        b"\0" * (discovery.MIN_MODEL_BYTES + 1))
    (root / "back").symlink_to(root, target_is_directory=True)

    found, _, _ = discovery.scan_model_files(roots=[("t", root)], max_files=100)
    assert len(found) == 1


def test_scan_is_bounded(tmp_path):
    """A huge cache must not stall startup, so the walk is capped."""
    big = tmp_path / "huge"
    big.mkdir()
    for index in range(40):
        (big / f"model-{index}-q4.gguf").write_bytes(b"")
    found, _, files = discovery.scan_model_files(roots=[("t", big)], max_files=10)
    assert files <= 10


def _local_provider(model_name: str = "m:1b") -> ProviderInfo:
    return ProviderInfo(
        id="ollama:11434", label="Ollama", kind=ProviderKind.LOCAL,
        flavour=LocalFlavour.OLLAMA, base_url="http://127.0.0.1:11434",
        healthy=True, models=[ModelInfo(name=model_name, provider="ollama")],
    )


def _result(providers, loose=None):
    return discovery.ScanResult(providers=list(providers), loose_models=list(loose or []))


def test_local_mode_uses_the_local_runtime():
    router = Router(mode="local", result=_result([_local_provider()]))
    route = router.route()
    assert route.ok and route.kind == "local"
    assert route.runtime is not None


def test_local_mode_never_falls_back_to_remote():
    """Asking for local must fail loudly rather than silently going remote."""
    router = Router(mode="local", result=_result([]))
    router.note_remote(True)
    route = router.route()
    assert not route.ok
    assert "local" in route.reason


def test_auto_prefers_local_when_available():
    router = Router(mode="auto", result=_result([_local_provider()]))
    router.note_remote(True)
    route = router.route()
    assert route.kind == "local"
    assert not route.fallback_used


def test_auto_falls_back_to_remote_and_says_so():
    router = Router(mode="auto", result=_result([]))
    router.note_remote(True)
    route = router.route()
    assert route.kind == "remote"
    assert route.fallback_used


def test_auto_with_nothing_available_reports_why():
    router = Router(mode="auto", result=_result([]))
    route = router.route()
    assert not route.ok
    assert route.reason


def test_remote_mode_ignores_local_availability():
    router = Router(mode="remote", result=_result([_local_provider()]))
    router.note_remote(True)
    route = router.route()
    assert route.kind == "remote"
    # The user is told what was set aside, rather than it passing silently.
    assert route.warning


def test_prefer_selects_the_named_provider():
    wanted = _local_provider()
    other = ProviderInfo(
        id="openai:1234", label="LM Studio", kind=ProviderKind.LOCAL,
        flavour=LocalFlavour.LMSTUDIO, base_url="http://127.0.0.1:1234", healthy=True,
        models=[ModelInfo(name="other:1b", provider="openai")],
    )
    router = Router(mode="local", prefer="LM Studio", result=_result([wanted, other]))
    assert router.route().provider.label == "LM Studio"


def test_unknown_mode_is_rejected_loudly():
    with pytest.raises(ValueError):
        Mode.parse("sideways")


def test_pick_model_prefers_a_usable_size():
    models = [
        ModelInfo(name="tiny:0.1b", provider="ollama", parameter_count=0.1),
        ModelInfo(name="good:7b", provider="ollama", parameter_count=7),
        ModelInfo(name="huge:400b", provider="ollama", parameter_count=400),
    ]
    router = Router(mode="local", result=_result(
        [ProviderInfo(id="o", label="O", kind=ProviderKind.LOCAL,
                      flavour=LocalFlavour.OLLAMA, healthy=True, models=models)]))
    assert router.pick_model(route=router.route()) == "good:7b"


def test_explicit_model_wins_over_ranking():
    router = Router(mode="local", result=_result([_local_provider("only:1b")]))
    assert router.pick_model("only:1b", route=router.route()) == "only:1b"


def test_discovery_never_raises_without_network():
    """Discovery runs on every startup; it must not fail loudly on timeout."""
    result = discovery.scan(deep=False, include_files=False, timeout=0.01)
    assert isinstance(result.providers, list)
    assert result.duration_ms >= 0
