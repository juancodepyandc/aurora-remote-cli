"""Local inference adapters.

Two wire formats cover essentially every local runtime in circulation:

* Ollama's ``/api/chat`` newline-delimited JSON
* the OpenAI ``/v1/chat/completions`` SSE protocol, spoken by llama.cpp,
  LM Studio, vLLM, LocalAI, KoboldCpp, Jan, GPT4All and llamafile

Both are implemented once here, so adding a runtime means adding a row to
:data:`aurora_cli.core.discovery.KNOWN_RUNTIMES`, not a new class.
"""
from __future__ import annotations

import json
from typing import Iterator

from aurora_cli.core.providers import (
    LocalFlavour,
    ModelInfo,
    ProviderInfo,
    ProviderKind,
    StreamChunk,
)


class RuntimeError_(Exception):
    """Raised when a local runtime cannot fulfil a request."""


class LocalRuntime:
    """Adapter over a discovered provider."""

    def __init__(self, info: ProviderInfo, api_key: str = ""):
        self.info = info
        self.api_key = api_key or ""

    # --- plumbing --------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self.api_key or self._env_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _env_key(self) -> str:
        import os

        from aurora_cli import brand

        name = self.info.api_key_env or ""
        if name and os.environ.get(name):
            return os.environ[name]
        for candidate in (brand.env("api_key"), brand.env_legacy("api_key")):
            value = os.environ.get(candidate, "").strip()
            if value:
                return value
        return ""

    def models(self) -> list[ModelInfo]:
        return list(self.info.models)

    # --- streaming -------------------------------------------------------

    def stream(self, model: str, messages: list[dict], *,
               temperature: float = 0.7, top_p: float = 0.95,
               max_tokens: int = 2048, stop: list[str] | None = None,
               system: str = "", timeout: float = 600.0) -> Iterator[StreamChunk]:
        """Yield answer increments. Raises :class:`RuntimeError_` on failure."""
        if not model:
            raise RuntimeError_("Aucun modèle sélectionné.")
        if not self.info.healthy:
            raise RuntimeError_(f"{self.info.label} est injoignable ({self.info.detail})")
        if self.info.flavour is LocalFlavour.OLLAMA:
            yield from self._stream_ollama(model, messages, temperature,
                                           top_p, max_tokens, system, timeout)
        else:
            yield from self._stream_openai(model, messages, temperature,
                                           top_p, max_tokens, stop, system, timeout)

    def complete(self, model: str, messages: list[dict], **options) -> str:
        """Non-streaming convenience wrapper."""
        chunks = []
        for chunk in self.stream(model, messages, **options):
            if chunk.error:
                raise RuntimeError_(chunk.error)
            chunks.append(chunk.text)
        return "".join(chunks)

    # --- Ollama ----------------------------------------------------------

    def _stream_ollama(self, model: str, messages: list[dict], temperature: float,
                       top_p: float, max_tokens: int, system: str,
                       timeout: float) -> Iterator[StreamChunk]:
        import httpx

        payload_messages = list(messages)
        if system:
            payload_messages = [{"role": "system", "content": system}] + payload_messages
        body = {
            "model": model,
            "messages": payload_messages,
            "stream": True,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "num_predict": max_tokens,
            },
        }
        try:
            with httpx.stream("POST", f"{self.info.base_url}/api/chat",
                              json=body, headers=self._headers(),
                              timeout=httpx.Timeout(timeout, connect=5.0)) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line or not line.strip():
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        yield StreamChunk(error=str(event["error"]), done=True)
                        return
                    piece = (event.get("message") or {}).get("content", "")
                    if piece:
                        yield StreamChunk(text=str(piece))
                    if event.get("done"):
                        yield StreamChunk(done=True, meta={
                            "total_duration": event.get("total_duration", 0),
                            "eval_count": event.get("eval_count", 0),
                        })
                        return
        except httpx.HTTPError as exc:
            raise RuntimeError_(f"Ollama : {exc}") from exc

    # --- OpenAI-compatible ----------------------------------------------

    def _stream_openai(self, model: str, messages: list[dict], temperature: float,
                       top_p: float, max_tokens: int, stop: list[str] | None,
                       system: str, timeout: float) -> Iterator[StreamChunk]:
        import httpx

        payload_messages = list(messages)
        if system:
            payload_messages = [{"role": "system", "content": system}] + payload_messages
        body: dict = {
            "model": model,
            "messages": payload_messages,
            "stream": True,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
        }
        if stop:
            body["stop"] = stop
        try:
            with httpx.stream("POST", f"{self.info.base_url}/v1/chat/completions",
                              json=body, headers=self._headers(),
                              timeout=httpx.Timeout(timeout, connect=5.0)) as response:
                response.raise_for_status()
                for raw in response.iter_lines():
                    if not raw:
                        continue
                    line = raw.strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        yield StreamChunk(done=True)
                        return
                    try:
                        event = json.loads(data)
                    except ValueError:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("error"):
                        message = event["error"]
                        yield StreamChunk(
                            error=str(message.get("message") if isinstance(message, dict) else message),
                            done=True,
                        )
                        return
                    for choice in event.get("choices") or []:
                        if not isinstance(choice, dict):
                            continue
                        piece = (choice.get("delta") or {}).get("content")
                        if piece:
                            yield StreamChunk(text=str(piece))
                        if choice.get("finish_reason"):
                            yield StreamChunk(done=True, meta={"finish": choice["finish_reason"]})
                            return
                yield StreamChunk(done=True)
        except httpx.HTTPError as exc:
            raise RuntimeError_(f"{self.info.label} : {exc}") from exc

    def describe(self) -> str:
        bits = [self.info.label, self.info.base_url]
        if self.info.version:
            bits.append(self.info.version)
        return " · ".join(bits)


def build_runtime(info: ProviderInfo) -> LocalRuntime:
    """Wrap a provider record in its adapter."""
    return LocalRuntime(info)


def from_scan(providers: list[ProviderInfo], *, prefer: str = "") -> LocalRuntime | None:
    """Pick a runtime from a discovery result.

    ``prefer`` matches on provider id, label or base URL, so a user can pin
    ``JOBIA_LOCAL_PROVIDER=ollama`` without knowing the generated ids.
    """
    healthy = [p for p in providers if p.healthy and p.kind is not ProviderKind.REMOTE]
    if not healthy:
        return None
    if prefer:
        needle = prefer.strip().lower()
        for provider in healthy:
            haystack = " ".join((provider.id, provider.label, provider.base_url)).lower()
            if needle in haystack:
                return build_runtime(provider)
        return None
    healthy.sort(key=lambda p: (not bool(p.models), p.flavour is not LocalFlavour.OLLAMA,
                                -len(p.models), p.latency_ms))
    return build_runtime(healthy[0])
