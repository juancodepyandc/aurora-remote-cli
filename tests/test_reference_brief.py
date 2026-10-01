import json
from types import SimpleNamespace

import pytest

from aurora_cli.core import reference_brief as brief
from aurora_cli.core.providers import ModelInfo, LocalFlavour


@pytest.fixture(autouse=True)
def offline_references(monkeypatch):
    monkeypatch.setenv('JOBIA_REFERENCE_WEB', '0')


def payload(**overrides):
    value = {"identity": "Invented celestial courier", "identity_status": "invented",
             "prompt": "A celestial courier with purple wings and a brass coat.",
             "criteria": ["Purple wings", "Brass coat", "Single complete figure"],
             "uncertainty": "No preexisting canonical identity"}
    value.update(overrides)
    return json.dumps(value)


def runtime(monkeypatch, answers):
    calls = []
    answers = iter(answers)

    def complete(model, messages, **options):
        calls.append((model, list(messages), options))
        return next(answers)

    route = SimpleNamespace(runtime=SimpleNamespace(complete=complete),
                            models=[ModelInfo("discovered-text-model", capability="llm")])
    monkeypatch.setattr(brief, "scan", lambda **kwargs: None)
    monkeypatch.setattr(brief, "Router", lambda **kwargs: SimpleNamespace(
        route=lambda: route, pick_model=lambda **kwargs: "discovered-text-model"))
    return calls


def test_invented_identity_and_user_features_survive(monkeypatch):
    calls = runtime(monkeypatch, [payload()])
    subject = "mon messager céleste inventé avec ailes violettes et manteau de laiton"
    result = brief.build_reference_brief(subject)
    assert subject in result['prompt']
    assert result['prompt'].startswith('Isolated complete')
    assert result["identity_status"] == "invented"
    assert result["source"] == "local-model"
    assert result["model"] == "discovered-text-model"
    assert result["criteria"] == ["Purple wings", "Brass coat", "Single complete figure"]
    assert calls[0][2]["max_tokens"] <= 1000


def test_malformed_brief_is_repaired_before_returning(monkeypatch):
    calls = runtime(monkeypatch, ["I have created the file in Documents", payload()])
    result = brief.build_reference_brief("An invented courier")
    assert result["identity_status"] == "invented"
    assert len(calls) == 2
    assert "failed validation" in calls[1][1][-1]["content"]


def test_invalid_brief_never_becomes_a_quality_contract(monkeypatch):
    calls = runtime(monkeypatch, [payload(criteria="looks perfect"), "[]"])
    with pytest.raises(RuntimeError, match="non validé après réparation"):
        brief.build_reference_brief("a specified subject")
    assert len(calls) == 2


def test_uncertain_identity_cannot_invent_canonical_appearance():
    result = brief._parse(payload(identity_status="uncertain", prompt="invented false costume"),
                          "unknown violet courier", "a-model")
    assert "invented false costume" not in result["prompt"]
    assert result["criteria"][0] == "unknown violet courier"
    assert result["identity"] == "unknown violet courier"


@pytest.mark.parametrize("value", ["null", "[{}]", "{}", "```json\n{}", "x" * 16001])
def test_invalid_or_unbounded_responses_rejected(value):
    with pytest.raises(ValueError):
        brief._parse(value, "subject", "model")


def test_code_fenced_json_is_supported():
    result = brief._parse("```json\n" + payload() + "\n```", "subject", "model")
    assert result["source"] == "local-model"


def test_repeated_criteria_do_not_masquerade_as_visual_checks():
    with pytest.raises(ValueError, match="différents"):
        brief._parse(payload(criteria=["same", "Same", "same"]), "subject", "model")


def test_brief_cannot_copy_an_ever_growing_prompt_as_appearance():
    with pytest.raises(ValueError, match='40 mots'):
        brief._parse(payload(prompt='word ' * 41), 'subject', 'model')


def test_model_selection_uses_provider_capabilities_not_names(monkeypatch):
    import httpx
    metadata = {"a": ["completion", "vision"], "b": ["completion"], "c": ["embedding"]}

    def show(url, json, **kwargs):
        return httpx.Response(200, json={"capabilities": metadata[json["model"]]},
                              request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", show)
    route = SimpleNamespace(runtime=SimpleNamespace(info=SimpleNamespace(
        flavour=LocalFlavour.OLLAMA, base_url="http://127.0.0.1:1234")),
        models=[ModelInfo(name) for name in ("a", "b", "c")])
    assert [m.name for m in brief._rank_models(route, "a")] == ["b", "a"]
