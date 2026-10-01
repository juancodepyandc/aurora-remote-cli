import contextlib
from types import SimpleNamespace

import pytest

from aurora_cli.core.providers import LocalFlavour, ProviderInfo, ProviderKind
from aurora_cli.core.runtime import LocalRuntime, RuntimeError_


def mock_http(monkeypatch, lines):
    import httpx
    calls = []
    def stream(*args, **kwargs):
        calls.append(kwargs['json'])
        response = SimpleNamespace(raise_for_status=lambda: None, iter_lines=lambda: iter(lines))
        return contextlib.nullcontext(response)
    monkeypatch.setattr(httpx, 'stream', stream)
    return calls


def runtime(flavour):
    return LocalRuntime(ProviderInfo('test', 'Test', ProviderKind.LOCAL,
                        flavour=flavour, base_url='http://127.0.0.1:1', healthy=True))


@pytest.mark.parametrize('flavour,lines', [
    (LocalFlavour.OLLAMA, ['{"message":{"content":"partial"}}']),
    (LocalFlavour.OPENAI, ['data: {"choices":[{"delta":{"content":"partial"}}]}']),
])
def test_connection_end_is_not_a_success(monkeypatch, flavour, lines):
    mock_http(monkeypatch, lines)
    with pytest.raises(RuntimeError_, match='confirmation de fin'):
        list(runtime(flavour).stream('m', []))


def test_ollama_reports_length_and_receives_context_budget(monkeypatch):
    calls = mock_http(monkeypatch, ['{"done":true,"done_reason":"length"}'])
    chunks = list(runtime(LocalFlavour.OLLAMA).stream('m', [], context_length=8192))
    assert chunks[-1].meta['done_reason'] == 'length'
    assert calls[0]['options']['num_ctx'] == 8192


@pytest.mark.parametrize('flavour,lines', [
    (LocalFlavour.OLLAMA, ['{"done":true,"done_reason":"length"}']),
    (LocalFlavour.OPENAI, ['data: {"choices":[{"delta":{},"finish_reason":"length"}]}']),
])
def test_complete_rejects_truncated_structured_results(monkeypatch, flavour, lines):
    mock_http(monkeypatch, lines)
    with pytest.raises(RuntimeError_, match='tronquée'):
        runtime(flavour).complete('m', [])
