"""Regressions at the selection, plugin and portable execution boundaries."""
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from aurora_cli import adapters, capability_probe, platforms
from aurora_cli.core import engine3d, machine, pipeline
from aurora_cli.engine_manager import AutonomousEngineManager, EngineRecord
from aurora_cli.evolution import Candidate, EvolutionLoop, TaskSpec
from test_evolution import candidate


@pytest.fixture
def loop(tmp_path):
    manager = AutonomousEngineManager()
    manager.registry._records = {}
    manager.registry.registry_path = tmp_path / "engines.json"
    manager.scan_local_installations = lambda: 0
    return EvolutionLoop(manager, state_path=tmp_path / "evolution.json")


def retain(candidate_, proof):
    candidate_.score = proof.score
    candidate_.checks = proof.checks
    candidate_.proof = proof.proof


def test_best_candidate_wins_even_when_discovered_last(loop, tmp_path):
    task = TaskSpec("3d")
    seen = []
    for name in ("first", "best"):
        loop.registry.add(EngineRecord(name, "3d", Path("/tmp") / name, "local", 0))

    def attempt(c):
        seen.append(c.name)
        retain(c, candidate(c.name, score=0.85 if c.name == "first" else 1.0,
                            spec=c.spec, tmp_path=tmp_path))

    assert loop.improve(task, attempt=attempt)["promoted"] == "best"
    assert seen == ["first", "best"]
    assert len([r for r in loop.log.entries() if r["step"] == "promote"]) == 1


def test_equal_scores_do_not_churn_or_prevent_autonomy_from_stopping(loop, tmp_path):
    task = TaskSpec("3d")
    incumbent = candidate("current", score=1.0, tmp_path=tmp_path)
    loop.promote(task, incumbent, "first")
    contender = candidate("equal", score=1.0, tmp_path=tmp_path)
    allowed, reason = loop.should_promote(task, contender)
    assert not allowed and "strictly better" in reason


def test_new_goal_does_not_inherit_another_goals_score(loop, tmp_path):
    # Both manifests have retained outputs and an explicit execution prompt.
    task = TaskSpec("3d", goal="red cube")
    incumbent = candidate("current", score=1.0, tmp_path=tmp_path)
    manifest = Path(incumbent.proof["manifest"])
    payload = json.loads(manifest.read_text())
    payload["execution"]["prompt"] = task.goal
    manifest.write_text(json.dumps(payload))
    incumbent.proof["sha256"] = capability_probe.file_hash(manifest)
    loop.promote(task, incumbent, "first")
    other_task = replace(task, goal="blue sphere")
    contender = candidate("other", score=0.9, tmp_path=tmp_path)
    manifest = Path(contender.proof["manifest"])
    payload = json.loads(manifest.read_text())
    payload["execution"]["prompt"] = other_task.goal
    manifest.write_text(json.dumps(payload))
    contender.proof["sha256"] = capability_probe.file_hash(manifest)
    assert loop.should_promote(other_task, contender)[0]
    assert not loop.should_promote(task, contender)[0]


def test_changed_machine_or_adapter_settings_require_fresh_comparison(loop, tmp_path):
    task = TaskSpec("3d")
    loop.promote(task, candidate("current", score=1.0, tmp_path=tmp_path), "first")
    contender = candidate("other", score=0.9, tmp_path=tmp_path)
    assert not loop.should_promote(task, contender)[0]
    previous = loop.environment
    loop.environment = previous | {"total_ram_gb": 128.0}
    assert loop.should_promote(task, contender)[0]
    loop.environment = previous
    loop.adapter_signature = "changed-runner-settings"
    assert loop.should_promote(task, contender)[0]


def test_corrupt_incumbent_evidence_cannot_block_a_working_candidate(loop, tmp_path):
    task = TaskSpec("3d")
    incumbent = candidate("current", score=1.0, tmp_path=tmp_path)
    loop.promote(task, incumbent, "first")
    output = Path(json.loads(Path(incumbent.proof["manifest"]).read_text())["output"])
    output.write_bytes(b"corrupted")
    assert loop.should_promote(task, candidate("other", score=0.9, tmp_path=tmp_path))[0]


def test_distinct_fixtures_have_independent_incumbents(loop, tmp_path):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"red cube")
    second.write_bytes(b"blue sphere")
    for name, fixture, score in (("current", first, 1.0), ("other", second, 0.9)):
        c = candidate(name, score=score, tmp_path=tmp_path)
        manifest = Path(c.proof["manifest"])
        payload = json.loads(manifest.read_text())
        payload["fixture_sha256"] = capability_probe.file_hash(fixture)
        manifest.write_text(json.dumps(payload))
        c.proof["sha256"] = capability_probe.file_hash(manifest)
        assert loop.should_promote(TaskSpec("3d", fixture=fixture), c)[0]
        loop.promote(TaskSpec("3d", fixture=fixture), c, "independent fixture")
    assert len(loop.state["evaluations"]["3d"]) == 2


@pytest.mark.parametrize("spec", [
    "attacker/hunyuan3d-2.1", "tencent/Hunyuan3D-2.1-incompatible",
    "attacker/stabilityai/sdxl-turbo", "stabilityai/sdxl-turbo-modified",
])
def test_model_substrings_do_not_claim_another_models_adapter(spec):
    capability = "3d" if "hunyuan" in spec.casefold() else "image"
    assert adapters.AdapterRegistry().resolve(capability, spec)[0] is None


def test_specific_adapter_beats_a_generic_adapter():
    registry = adapters.AdapterRegistry({"runner": {
        "generic": {"capability": "image", "argv": ["generic"]},
        "specific": {"capability": "image", "specs": ["author/model"], "argv": ["specific"]},
    }})
    assert registry.resolve("image", "author/model")[0].id == "specific"


def test_argv_array_preserves_windows_paths():
    registry = adapters.AdapterRegistry({"runner": {
        "windows": {"capability": "image", "argv": [r"C:\Program Files\Runtime\python.exe", "{output}"]},
    }})
    runner = registry.resolve("image", "anything")[0]
    assert registry.build(runner, output=r"C:\My output\image.png") == [
        r"C:\Program Files\Runtime\python.exe", r"C:\My output\image.png"]


def test_image_execution_obeys_manifest_and_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(adapters, "load_manifest", lambda: {"runner": {"custom": {
        "capability": "image", "specs": ["author/model"],
        "argv": ["{interpreter}", "custom-worker", "{output}", "{prompt}", "--quality={quality}"],
        "env": ["JOBIA_TEST_BACKEND=chosen"], "parameters": {"quality": 42},
    }}})
    monkeypatch.setattr(pipeline, "ensure_model", lambda *a: (tmp_path, Path("python")))
    output = tmp_path / "image.png"
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs["env"]))
        output.write_bytes(b"generated image")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(pipeline.subprocess, "run", execute)
    result = pipeline.stage_generate_image("red sphere --extra", output, model_name="author/model")
    assert result.status == "done"
    assert calls[0][0] == ["python", "custom-worker", str(output), "red sphere --extra", "--quality=42"]
    assert calls[0][1]["JOBIA_TEST_BACKEND"] == "chosen"


def test_mesh_execution_obeys_manifest_and_environment(monkeypatch, tmp_path):
    from aurora_cli.core import machine
    # Hardware probing also uses subprocess.run; keep this adapter test local
    # to its worker instead of replacing GPU discovery with the worker stub.
    monkeypatch.setattr(machine, "profile", lambda: SimpleNamespace(
        os_name="linux", arch="x86_64", accelerator="cuda", vram_bytes=32 * 1024**3,
        ram_total_bytes=64 * 1024**3, ram_available_bytes=64 * 1024**3,
        python="3.12", libc="glibc", libc_version="2.39"))
    monkeypatch.setattr(adapters, "load_manifest", lambda: {"runner": {"custom": {
        "capability": "3d", "specs": ["author/model"],
        "texturing": True,
        "argv": ["{interpreter}", "custom-mesh", "{output}", "--quality={quality}", "{paint_flag}"],
        "env": ["JOBIA_TEST_BACKEND=mesh"], "parameters": {"quality": 81},
    }}})
    output = tmp_path / "mesh.glb"
    calls = []

    def execute(command, **kwargs):
        calls.append((command, kwargs["env"]))
        output.write_bytes(b"glTF" + b"x" * 32)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(engine3d.subprocess, "run", execute)
    engine = engine3d.Engine3D("model", tmp_path, Path("python"), tmp_path, True)
    assert engine3d.generate_mesh(engine, tmp_path / "image.png", output, model_ref="author/model")[0]
    assert calls[0][0] == ["python", "custom-mesh", str(output), "--quality=81", "--paint"]
    assert calls[0][1]["JOBIA_TEST_BACKEND"] == "mesh"


def test_empty_negative_prompt_leaves_a_valid_argument():
    registry = adapters.AdapterRegistry()
    runner = registry.resolve("image", "stabilityai/sdxl-turbo")[0]
    command = registry.build(runner, interpreter="python", worker="worker", model_dir="model",
                             prompt="sphere", output="output", seed=0, negative="", model_spec="stabilityai/sdxl-turbo")
    assert "--negative=" in command


def test_requested_texture_cannot_silently_become_geometry_only(monkeypatch, tmp_path):
    def unexpected(*args, **kwargs):
        raise AssertionError('Missing texturing must fail before starting an expensive reconstruction')
    monkeypatch.setattr(engine3d.subprocess, 'run', unexpected)
    engine = engine3d.Engine3D('model', tmp_path, Path('python'), tmp_path, False)
    ok, log = engine3d.generate_mesh(engine, tmp_path / 'image.png', tmp_path / 'mesh.glb')
    assert not ok and 'Texturation demandée' in log


def test_an_installed_domain_probe_can_generate_retained_evidence(monkeypatch, tmp_path):
    policy = tmp_path / "quality.toml"
    policy.write_text('''[evaluation.audio]
metric = "audio-readiness-v1"
required_checks = ["output_nonempty", "audio_valid"]
[evaluation.audio.weights]
output_nonempty = 0.2
audio_valid = 0.8
''')
    monkeypatch.setenv("JOBIA_QUALITY_POLICY", str(policy))
    monkeypatch.setattr(adapters, "load_manifest", lambda: {"runner": {"audio": {
        "capability": "audio", "specs": ["author/voice"], "argv": ["audio-worker"],
    }}})

    def domain_probe(c, **kwargs):
        import time
        output = tmp_path / "probe.wav"
        output.write_bytes(b"RIFF test output")
        c.install_path = tmp_path
        return capability_probe._record(
            c, output, tmp_path, time.monotonic(),
            {"output_nonempty": True, "audio_valid": True}, {"model_ref": c.spec})

    entry = SimpleNamespace(load=lambda: domain_probe)
    monkeypatch.setattr(capability_probe.metadata, "entry_points", lambda **kwargs: [entry])
    c = Candidate("voice", "audio", "author/voice", "local", score=0.1)
    capability_probe.run_probe("audio", c)
    assert c.score == 1.0
    proof = json.loads(Path(c.proof["manifest"]).read_text())
    assert proof["metric"] == "audio-readiness-v1" and proof["spec"] == "author/voice"


def test_duplicate_domain_probes_are_reported(monkeypatch):
    monkeypatch.setattr(capability_probe.metadata, "entry_points", lambda **kwargs: [object(), object()])
    with pytest.raises(RuntimeError, match="Multiple installed probes"):
        capability_probe.probe_for("audio")


def test_unrecognized_os_is_not_classified_as_linux(monkeypatch):
    monkeypatch.setattr(platforms.platform, "system", lambda: "FreeBSD")
    assert platforms.detect(gpu="cpu").system == "freebsd"


def test_unknown_libc_is_not_assumed_to_be_glibc(monkeypatch):
    monkeypatch.setattr(platforms.platform, "libc_ver", lambda: ("", ""))
    monkeypatch.setattr(Path, "glob", lambda *args: iter(()))
    assert platforms._detect_libc("linux") == ""
    assert not platforms.Resolver().resolve("torch", platforms.detect(
        system="linux", arch="x86_64", libc="", gpu="cpu")).ok


def test_rocm_is_not_reported_as_nvidia_cuda(monkeypatch):
    import sys
    torch = SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: True),
                            version=SimpleNamespace(hip="6.3"))
    monkeypatch.setitem(sys.modules, "torch", torch)
    assert platforms._detect_gpu() == "rocm"


def test_accelerator_specific_target_beats_general_target():
    resolver = platforms.Resolver({"target": {
        "general": {"name": "general", "system": "linux", "arch": "x86_64", "libc": "gnu"},
        "gpu": {"name": "gpu", "system": "linux", "arch": "x86_64", "libc": "gnu", "gpu": "rocm"},
    }})
    assert resolver.target(platforms.detect(system="linux", arch="x86_64", libc="gnu", gpu="rocm"))["name"] == "gpu"


def test_memory_measurement_works_without_posix_sysconf(monkeypatch):
    import psutil
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(total=32 * 1024**3, available=12 * 1024**3))
    monkeypatch.delattr(machine.os, "sysconf", raising=False)
    assert machine._total_ram_bytes() == 32 * 1024**3
    assert machine._free_ram_bytes() == 12 * 1024**3


def test_learned_image_choice_reaches_production_and_respects_free_memory(loop, tmp_path, monkeypatch):
    import time
    from aurora_cli.core import locations

    loop.state_path = locations.data_dir() / "evolution.json"
    loop.registry.registry_path = locations.data_dir() / "engine_registry.json"
    model = tmp_path / "installed-model"
    model.mkdir()
    (model / "model_index.json").write_text("{}")
    output = tmp_path / "image.png"
    output.write_bytes(b"retained probe output")
    c = Candidate("proven-sdxl", "image", "stabilityai/stable-diffusion-xl-base-1.0", "local", install_path=model)
    capability_probe._record(c, output, tmp_path, time.monotonic(),
                             {"output_nonempty": True, "image_valid": True, "not_flat": True},
                             {"model_ref": c.spec})
    task = TaskSpec("image", required_checks=("output_nonempty", "image_valid", "not_flat"))
    loop.promote(task, c, "proven candidate")
    assert loop.recommendation("image").spec == c.spec
    host = machine.MachineProfile(total_ram_gb=64, free_ram_gb=40, free_disk_gb=200, ram_pressure=0.625)
    monkeypatch.setattr(pipeline, "profile", lambda: host)
    assert pipeline.select_image_model() == c.spec
    host.free_ram_gb = 10  # Eight usable GB: SDXL's ten-GB budget no longer fits.
    assert pipeline.select_image_model() == "stabilityai/sdxl-turbo"
    verified_at = loop.current("image")["verified_at"]
    loop.current("image")["verified_at"] = 0
    assert loop.recommendation("image") is None
    loop.current("image")["verified_at"] = verified_at
    output.write_bytes(b"changed output")
    assert loop.recommendation("image") is None


def test_image_cache_directory_does_not_replace_model_identity():
    registry = adapters.AdapterRegistry()
    runner = registry.resolve("image", "stabilityai/sdxl-turbo")[0]
    command = registry.build(runner, interpreter="python", worker="worker",
                             model_dir="snapshots/123456", prompt="sphere", output="image.png",
                             seed=0, negative="", model_spec="stabilityai/sdxl-turbo")
    assert command[command.index("--model-spec") + 1] == "stabilityai/sdxl-turbo"


def test_apple_silicon_can_resolve_torch_before_gpu_runtime_is_installed():
    host = platforms.detect(system="darwin", arch="arm64", libc="", gpu="cpu")
    assert platforms.Resolver().resolve("torch", host).ok


@pytest.mark.parametrize("requirement,version,expected", [
    (">=3.10,<3.13", (3, 12), True),
    (">=3.10,<3.13", (3, 13), False),
    (">=3.10,!=3.11.*", (3, 11), False),
    ("==3.11.*", (3, 12), False),
])
def test_python_constraints_enforce_upper_bounds_and_exclusions(requirement, version, expected):
    resolver = platforms.Resolver({"target": {"portable": {
        "python": requirement, "package": {"torch": ">=2.2,<2.3"},
    }}})
    host = replace(platforms.detect(gpu="cpu"), python=version)
    assert resolver.resolve("torch", host).ok is expected


def test_invalid_platform_constraints_are_reported():
    resolver = platforms.Resolver({"target": {"portable": {"python": "newest", "package": {"torch": ">=2.4"}}}})
    answer = resolver.resolve("torch", platforms.detect(gpu="cpu"))
    assert not answer.ok and "invalid platform constraint" in answer.reason


def test_intel_mac_uses_declared_legacy_stack_and_reports_newer_python():
    host = replace(platforms.detect(system="darwin", arch="x86_64", libc="", gpu="cpu"), python=(3, 12))
    answer = platforms.Resolver().resolve("torch", host)
    assert answer.ok and "<2.3" in answer.constraint
    assert not platforms.Resolver().resolve("torch", replace(host, python=(3, 13))).ok


def test_fresh_equal_result_renews_evidence_without_false_promotion(loop, tmp_path):
    import time

    model = tmp_path / "model"
    model.mkdir()
    c = Candidate("image-engine", "image", "stabilityai/sdxl-turbo", "local", install_path=model)
    output = tmp_path / "old.png"
    output.write_bytes(b"old valid output")
    checks = {"output_nonempty": True, "image_valid": True, "not_flat": True}
    capability_probe._record(c, output, tmp_path, time.monotonic(), checks, {"model_ref": c.spec})
    task = TaskSpec("image", required_checks=tuple(checks))
    loop.promote(task, c, "first")
    old_promotion = loop.current("image")["promoted_at"]
    # Expiration changes evidence freshness, not the incumbent's score.
    loop.current("image")["verified_at"] = 0
    assert loop.recommendation("image") is None
    fresh = tmp_path / "fresh"
    fresh.mkdir()

    def attempt(candidate_):
        image = fresh / "new.png"
        image.write_bytes(b"new valid output")
        capability_probe._record(candidate_, image, fresh, time.monotonic(), checks,
                                 {"model_ref": candidate_.spec})

    assert loop.improve(task, attempt=attempt)["promoted"] is None
    assert loop.recommendation("image") is not None
    assert loop.current("image")["promoted_at"] == old_promotion
    assert loop.current("image")["proof"]["manifest"] == str(fresh / "probe.json")
    assert any(row["step"] == "verify" and row["outcome"] == "refreshed" for row in loop.log.entries())
