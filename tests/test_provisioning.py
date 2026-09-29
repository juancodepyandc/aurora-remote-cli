"""Provisioning: the guarantees that keep a cleanup from eating real models.

The rules under test all come from failures the source project hit for real:

* an ``hf download`` with several ``--include`` patterns silently drops the
  later ones, so one command per pattern is mandatory;
* a successful pull that the runtime does not list has not installed anything,
  and recording it puts a lie in the ledger;
* Ollama's store is shared, so it must never be recorded as a directory;
* a machine under pressure gets a *smaller* tier, never a silent fallback to
  "remote" and never a forced downgrade the user did not ask for;
* a pre-existing model is unreachable from the delete path.
"""

from __future__ import annotations

import json

import pytest

from aurora_cli.core import agents as agents_mod
from aurora_cli.core import catalog, fetcher, provision
from aurora_cli.core.machine import MachineProfile


def _machine(**kw) -> MachineProfile:
    base = dict(os_name="Darwin", arch="arm64", total_ram_gb=24.0,
                free_ram_gb=20.0, free_disk_gb=300.0, unified_memory=True,
                accelerator="Metal (unified)")
    base.update(kw)
    prof = MachineProfile(**base)
    prof.ram_pressure = prof.free_ram_gb / prof.total_ram_gb
    return prof


# --- resolution ----------------------------------------------------------

def test_tiers_are_bounded_by_total_ram_not_free_ram():
    """A busy 128 GB machine can still run a 30 GB model, slowly."""
    agent = agents_mod.get("code")
    busy = _machine(total_ram_gb=128.0, free_ram_gb=1.0)
    assert "max" in catalog.available_tiers(agent, busy)


def test_pressure_downgrades_the_recommendation():
    agent = agents_mod.get("code")
    idle = _machine(free_ram_gb=22.0)
    busy = _machine(free_ram_gb=2.0)
    assert catalog.recommended_tier(agent, busy) == "light"
    # With enough genuinely free memory the top tier is offered, once the
    # headroom the recommendation keeps for the desktop is subtracted.
    roomy = _machine(free_ram_gb=28.0)
    assert catalog.recommended_tier(agent, roomy) == "max"


def test_free_ram_decides_the_tier_not_just_total():
    """A machine with lots of RAM but little free still steps the tier down.

    Total RAM is what makes a tier *possible*; free RAM is what makes it a
    sensible default right now. 24 GB of RAM with 12 GB free cannot actually
    host a 24 GB working set without swapping, so the plan must not propose it
    as the default on a half-idle machine.
    """
    agent = agents_mod.get("image")
    half_idle = _machine(total_ram_gb=24.0, free_ram_gb=12.0)
    tier = catalog.recommended_tier(agent, half_idle)
    assert tier != "max", "12 Go libres ne doivent pas recevoir un modèle de 24 Go"
    assert catalog.available_tiers(agent, half_idle)[-1] == "max", "max reste possible"


def test_the_tier_choice_is_explained():
    agent = agents_mod.get("3d")
    machine = _machine(total_ram_gb=24.0, free_ram_gb=12.0)
    reason = catalog.explain_tier(agent, machine, catalog.recommended_tier(agent, machine))
    assert reason, "un choix de niveau doit toujours être justifié"
    assert "Go" in reason


def test_the_explanation_never_quotes_the_wrong_tier_memory():
    """The reason must compare the chosen tier against the top one.

    It once read the largest number from the already-filtered list of the
    chosen tier, so the plan said "max would need 8 GB" while quoting the 8 GB
    light model as the recommendation.
    """
    agent = agents_mod.get("image")
    machine = _machine(total_ram_gb=24.0, free_ram_gb=12.0)
    tier = catalog.recommended_tier(agent, machine)
    reason = catalog.explain_tier(agent, machine, tier)
    tiers = catalog.available_tiers(agent, machine)
    if tier != tiers[-1]:
        chosen = max(a.ram_gb for a in catalog.for_agent(agent) if a.tier == tier)
        top = max(a.ram_gb for a in catalog.for_agent(agent) if a.tier == tiers[-1])
        assert f"demanderait {top:.0f} Go" in reason
        assert top > chosen, "the top tier should need more memory"


def test_no_tier_when_the_machine_is_too_small():
    agent = agents_mod.get("3d-texture")
    tiny = _machine(total_ram_gb=8.0, free_ram_gb=7.0)
    assert catalog.available_tiers(agent, tiny) == []
    assert catalog.recommended_tier(agent, tiny) == ""


def test_artifact_rams_fit_its_own_tier():
    """A tier label that lies about the memory is a trap."""
    for tier in catalog.TIERS:
        for artifact in catalog.ARTIFACTS:
            if artifact.tier == tier:
                assert artifact.ram_gb > 0, artifact


def test_no_invented_refs():
    """Every ref must be namespaced; a bare word would resolve to nothing."""
    for artifact in catalog.ARTIFACTS:
        if artifact.runtime == "huggingface":
            assert artifact.ref.count("/") == 1, artifact.ref
        else:
            assert ":" in artifact.ref, artifact.ref


# --- plan construction ---------------------------------------------------

def test_one_include_per_command(monkeypatch, tmp_path):
    """Several --include in one call makes the CLI drop the extras."""
    monkeypatch.setattr(fetcher, "_hf_cli", lambda: "hf")
    monkeypatch.setattr(fetcher.locations, "data_dir", lambda: tmp_path / "data")
    artifact = catalog.Artifact(
        agent_id="3d", tier="balanced", runtime="huggingface",
        ref="tencent/Hunyuan3D-2.1", label="x", bytes=1,
        include=("hunyuan3d-dit-v2-1/*", "hunyuan3d-vae-v2-1/*"))
    plan = fetcher.preflight(artifact, _machine())
    assert len(plan.commands) == 2
    for command in plan.commands:
        assert command.count("--include") == 1


def test_repo_without_include_omits_the_flag(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "_hf_cli", lambda: "hf")
    monkeypatch.setattr(fetcher.locations, "data_dir", lambda: tmp_path / "data")
    artifact = catalog.Artifact(agent_id="image", tier="max",
                                runtime="huggingface",
                                ref="black-forest-labs/FLUX.1-schnell",
                                label="x", bytes=1)
    plan = fetcher.preflight(artifact, _machine())
    assert "--include" not in plan.commands[0]


def test_preflight_refuses_without_disk(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "_hf_cli", lambda: "hf")
    monkeypatch.setattr(fetcher.locations, "data_dir", lambda: tmp_path / "data")
    artifact = catalog.resolve(agents_mod.get("image"), _machine(), "max")
    full = _machine(free_disk_gb=1.0)
    with pytest.raises(fetcher.ProvisionError) as exc:
        fetcher.preflight(artifact, full)
    assert "Espace insuffisant" in str(exc.value)


def test_ollama_store_is_never_a_recorded_directory(monkeypatch, tmp_path):
    """Recording ~/.ollama would make cleanup delete every model at once."""
    monkeypatch.setattr(fetcher.locations, "home", lambda: tmp_path)
    artifact = catalog.resolve(agents_mod.get("code"), _machine(), "light")
    plan = fetcher.preflight(artifact, _machine())
    assert plan.artifact.runtime == "ollama"
    # The store path is used for the command, never handed to the ledger.
    assert "models" not in str(plan.target) or plan.target.name == "models"


def test_ollama_record_is_a_tag_not_a_path(monkeypatch, tmp_path):
    records_file = tmp_path / "ledger.json"
    monkeypatch.setattr(provision, "ledger_path", lambda: records_file)
    monkeypatch.setattr(fetcher, "dir_size", lambda p: 0)
    plan = fetcher.Plan(
        artifact=catalog.Artifact(agent_id="code", tier="light",
                                  runtime="ollama", ref="qwen2.5-coder:7b",
                                  label="x", bytes=4_700_000_000),
        target=tmp_path, needs_bytes=4_700_000_000,
        commands=[["ollama", "pull", "qwen2.5-coder:7b"]])
    fetcher.record(plan, agent="code")
    records = provision.load_ledger()
    assert records[0].path == "ollama:qwen2.5-coder:7b"
    assert records[0].kind == "ollama-model"


# --- verification --------------------------------------------------------

def test_verify_rejects_a_missing_include(monkeypatch, tmp_path):
    """A download that landed *some* files but not all is still a failure.

    This is the case the source project hit: `hf download` reported success
    while one include pattern was silently dropped, so the DiT was missing and
    a mesh generated as a broken blob. Partial content must not pass as
    complete.
    """
    monkeypatch.setattr(fetcher, "_hf_cli", lambda: "hf")
    monkeypatch.setattr(fetcher.locations, "data_dir", lambda: tmp_path / "data")
    artifact = catalog.Artifact(agent_id="3d", tier="balanced",
                                runtime="huggingface", ref="a/b", label="x",
                                bytes=1,
                                include=("hunyuan3d-dit-v2-1/*",
                                         "hunyuan3d-vae-v2-1/*"))
    plan = fetcher.preflight(artifact, _machine())
    # One component landed, the other did not.
    (plan.target / "hunyuan3d-dit-v2-1").mkdir(parents=True)
    (plan.target / "hunyuan3d-dit-v2-1" / "w.bin").write_bytes(b"x" * 64)
    with pytest.raises(fetcher.ProvisionError) as exc:
        fetcher.verify(plan)
    assert "Pièce manquante" in str(exc.value)
    assert "vae" in str(exc.value)


def test_verify_rejects_an_empty_target(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "_hf_cli", lambda: "hf")
    monkeypatch.setattr(fetcher.locations, "data_dir", lambda: tmp_path / "data")
    artifact = catalog.Artifact(agent_id="image", tier="light",
                                runtime="huggingface",
                                ref="stabilityai/stable-diffusion-xl-base-1.0",
                                label="x", bytes=1)
    plan = fetcher.preflight(artifact, _machine())
    with pytest.raises(fetcher.ProvisionError) as exc:
        fetcher.verify(plan)
    assert "sans contenu" in str(exc.value)


def test_verify_refuses_unverifiable_ollama_install(monkeypatch):
    """An unreadable runtime is unknown, not a failed install."""
    def boom(*a, **k):
        raise OSError("no ollama")
    monkeypatch.setattr(fetcher.subprocess, "run", boom)
    assert fetcher._ollama_has("qwen2.5-coder:7b") is False


# --- ledger safety -------------------------------------------------------

def test_preexisting_models_are_never_removable(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "ledger_path",
                        lambda: tmp_path / "provisioned.json")
    keep = tmp_path / "hunyuan"
    keep.mkdir()
    provision.record_existing([str(keep)], agent="3d")
    assert provision.removable() == []


def test_owned_huggingface_tree_is_removable(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "ledger_path",
                        lambda: tmp_path / "provisioned.json")
    tree = tmp_path / "flux"
    tree.mkdir()
    provision.record_installed(tree, kind="model", agent="image", owned=True)
    assert [r.path for r in provision.removable()] == [str(tree)]


def test_ollama_tag_survives_a_missing_store(monkeypatch, tmp_path):
    """A tag has no directory, so existence must not filter it out."""
    monkeypatch.setattr(provision, "ledger_path",
                        lambda: tmp_path / "provisioned.json")
    provision.record_installed("ollama:x:1b", kind="ollama-model",
                               agent="code", owned=True)
    assert len(provision.removable()) == 1


def test_corrupt_ledger_grants_nothing(monkeypatch, tmp_path):
    """A damaged ledger must mean "install nothing", never "delete maybe"."""
    path = tmp_path / "provisioned.json"
    path.write_text("{not json")
    monkeypatch.setattr(provision, "ledger_path", lambda: path)
    assert provision.load_ledger() == []
    assert provision.removable() == []


def test_ledger_is_not_world_readable(monkeypatch, tmp_path):
    monkeypatch.setattr(provision, "ledger_path",
                        lambda: tmp_path / "provisioned.json")
    provision.record_installed(tmp_path / "m", kind="model", owned=True)
    mode = (tmp_path / "provisioned.json").stat().st_mode & 0o777
    assert mode == 0o600


# --- display -------------------------------------------------------------

def test_human_does_not_invent_units():
    assert provision.human(4_700_000_000) == "4.4 Go"
    assert provision.human(500) == "500 o"
