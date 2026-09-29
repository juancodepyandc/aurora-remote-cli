"""The request-to-plan layer.

These are the tests that decide whether `jobia ask` is intelligent or just a
pretty catalogue browser: does a compound sentence get split, does a shared
dependency get counted once, does an encoder masquerading as a text model get
rejected, and does a missing input get called out instead of silently
proposing a download.
"""

import pytest

from aurora_cli.core import agents as agents_mod
from aurora_cli.core import intent
from aurora_cli.core.intent import recommend


def _machine(total_ram_gb=24.0, free_ram_gb=20.0):
    class M:
        pass
    m = M()
    m.total_ram_gb = total_ram_gb
    m.free_ram_gb = free_ram_gb
    m.disk_free_gb = 500.0
    m.under_pressure = free_ram_gb < total_ram_gb * 0.15
    return m


def _model(name, capability="", size=1_000_000_000):
    class M:
        pass
    m = M()
    m.name = name
    m.capability = capability
    m.size_bytes = size
    m.format = "safetensors"
    return m


# --- compound requests -------------------------------------------------------

def test_a_sentence_with_two_jobs_yields_two_agents():
    found = {agent.id for agent, _ in intent.match_agents(
        "genere une image de chaise puis fais un modele 3D")}
    assert "image" in found
    assert "3d" in found


def test_a_compound_request_plans_both_steps():
    plan = recommend("genere une image de chaise puis fais un modele 3D",
                     _machine(), [])
    ids = {s.agent.id for s in plan.steps}
    assert "image" in ids
    assert "3d" in ids


def test_and_also_splits_requests():
    found = {agent.id for agent, _ in intent.match_agents("write code and summarize this")}
    assert "code" in found
    assert "resume" in found


def test_the_longest_matching_keyword_wins():
    """`modele 3d` must beat a shorter overlapping word."""
    found = {agent.id for agent, _ in intent.match_agents("je veux un modele 3D")}
    assert "3d" in found


# --- accents and language ----------------------------------------------------

def test_accents_do_not_hide_a_capability():
    found = {agent.id for agent, _ in intent.match_agents("génère une image de chat")}
    assert "image" in found


def test_english_is_understood():
    found = {agent.id for agent, _ in intent.match_agents("create an image of a cat")}
    assert "image" in found


# --- capability matching -----------------------------------------------------

def test_an_image_encoder_does_not_satisfy_a_text_agent():
    """The bug that made a plan contradict itself.

    dinov2-giant is a vision encoder. Treating it as a language model made the
    summarising agent look installed while the plan still proposed downloading
    a text model.
    """
    plan = recommend("resume ce document", _machine(),
                     [_model("facebook/dinov2-giant", "vision")])
    step = next(s for s in plan.steps if s.agent.id == "resume")
    assert not step.satisfied, "un encodeur d'image ne sait pas résumer"
    assert plan.headline() != "Tout ce qu'il faut est déjà sur cette machine."


def test_a_vision_language_model_can_answer_text():
    found = recommend("resume ce document", _machine(),
                      [_model("qwen3-vl:30b", "vision")])
    step = next(s for s in found.steps if s.agent.id == "resume")
    assert step.satisfied


def test_a_3d_pipeline_does_not_satisfy_an_image_agent():
    plan = recommend("genere une image", _machine(),
                     [_model("hunyuan3d-dit-v2-1", "3d")])
    step = next(s for s in plan.steps if s.agent.id == "image")
    assert not step.satisfied


def test_a_mesh_generator_does_not_satisfy_the_texturing_agent():
    """Both are 3D, but one does not substitute for the other."""
    plan = recommend("texture mon maillage", _machine(),
                     [_model("hunyuan3d-dit-v2-1", "3d")])
    step = next(s for s in plan.steps if s.agent.id == "3d-texture")
    assert not step.satisfied


def test_installed_models_are_reported_not_proposed_for_download():
    plan = recommend("fais un modele 3D", _machine(), [
        _model("hunyuan3d-dit-v2-1", "3d"),
        _model("hunyuan3d-vae-v2-1", "3d"),
    ])
    step = next(s for s in plan.steps if s.agent.id == "3d")
    assert step.satisfied
    assert step.needs_download is False


# --- prerequisites -----------------------------------------------------------

def test_no_prerequisite_is_added_when_the_step_is_already_ready():
    """A 3D model on disk needs no vision model set up for it."""
    plan = recommend("fais un modele 3D", _machine(), [
        _model("hunyuan3d-dit-v2-1", "3d"),
        _model("hunyuan3d-vae-v2-1", "3d"),
    ])
    assert not any("prérequis" in s.because for s in plan.steps)


def test_a_pending_3d_job_with_a_picture_pulls_in_reading_it_first():
    """The 3D step needs the picture read, so a reader is part of the plan.

    It is matched from the word "image" itself rather than labelled as a
    prerequisite, because the request names the picture directly. Either way
    the reader must be present exactly once.
    """
    plan = recommend("fais un modele 3D de cette image", _machine(), [])
    ids = [s.agent.id for s in plan.steps]
    assert "3d" in ids
    assert "vision" in ids
    assert ids.count("vision") == 1, "le lecteur ne doit pas être proposé deux fois"


def test_generating_an_image_does_not_require_a_reader():
    """The image is the output here, so a vision model is not a prerequisite.

    Treating the word "image" as an input made a single picture request plan
    two models and about 12 GB.
    """
    plan = recommend("genere une image de chaise", _machine(), [])
    ids = [s.agent.id for s in plan.steps]
    assert ids.count("image") == 1
    assert "vision" not in ids


def test_a_3d_request_without_a_source_is_flagged():
    plan = recommend("fais un modele 3D", _machine(), [])
    step = next(s for s in plan.steps if s.agent.id == "3d")
    assert "source" in step.because


# --- download accounting -----------------------------------------------------

def test_a_shared_dependency_is_downloaded_once():
    """Two jobs needing the same conditioner cost one download, not two."""
    plan = recommend("genere une image puis fais un modele 3D", _machine(), [])
    refs = [a.ref for a in plan.planned_downloads()]
    assert len(refs) == len(set(refs)), f"doublon dans le plan : {refs}"


def test_the_plan_never_lists_a_download_for_a_satisfied_step():
    plan = recommend("fais un modele 3D", _machine(), [
        _model("hunyuan3d-dit-v2-1", "3d"),
        _model("hunyuan3d-vae-v2-1", "3d"),
    ])
    satisfied_step = next(s for s in plan.steps if s.agent.id == "3d")
    assert satisfied_step.artifact.ref not in [a.ref for a in plan.planned_downloads()] \
        or satisfied_step.satisfied


# --- unknown requests --------------------------------------------------------

def test_an_unknown_request_says_so_instead_of_guessing():
    plan = recommend("fais-moi unGateauNormal", _machine(), [])
    assert plan.unknown
    assert plan.steps == []
    assert plan.note


def test_an_empty_request_does_not_crash():
    plan = recommend("", _machine(), [])
    assert plan.unknown


# --- tiers and machine fit ---------------------------------------------------

def test_the_recommendation_follows_the_machine_not_the_wish():
    small = _machine(total_ram_gb=8.0, free_ram_gb=6.0)
    large = _machine(total_ram_gb=64.0, free_ram_gb=50.0)
    from aurora_cli.core import catalog
    code = agents_mod.get("code")
    assert catalog.recommended_tier(code, small) != catalog.recommended_tier(code, large)


def test_a_machine_too_small_for_a_job_is_reported_as_not_local():
    plan = recommend("texture mon maillage", _machine(total_ram_gb=8.0, free_ram_gb=6.0), [])
    step = next(s for s in plan.steps if s.agent.id == "3d-texture")
    assert not step.local_possible
    assert step.blocked_reason


def test_every_agent_is_reachable_from_its_own_name():
    """The catalogue and the parser must not drift apart."""
    for agent in agents_mod.AGENTS:
        assert agent.keywords, f"{agent.id} n'a aucun mot-clé"
        found = {a.id for a, _ in intent.match_agents(agent.keywords[0])}
        assert agent.id in found, f"{agent.id} ne se reconnaît pas lui-même"
