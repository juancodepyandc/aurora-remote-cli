"""Provider base types.

A provider is anything that can answer a chat completion: a local runtime on
this machine, or the remote bridge. Both are described by the same record so
the router, the ``models`` table and the fallback logic never need to know
which is which.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator, Protocol


class ProviderKind(str, Enum):
    """Where a provider lives, which decides how it is allowed to behave."""

    LOCAL = "local"
    REMOTE = "remote"
    CUSTOM = "custom"


class LocalFlavour(str, Enum):
    """Recognised local runtimes, so we can speak their own dialect."""

    OLLAMA = "ollama"
    OPENAI = "openai"
    LLAMACPP = "llamacpp"
    LMSTUDIO = "lmstudio"
    UNKNOWN = "unknown"


@dataclass
class ModelInfo:
    """One model, as reported by whichever runtime owns it."""

    name: str
    provider: str = ""
    size_bytes: int = 0
    parameter_count: float = 0.0
    quantization: str = ""
    family: str = ""
    #: On-disk weight format (gguf, safetensors, bin, onnx...). Optional,
    #: and only filled in by filesystem discovery where the file is named.
    weight_format: str = ""
    #: What the model can do: "llm", "3d", "vision", "embedding", "unknown".
    #: A provisioner needs this to answer "do I already have a 3D pipeline?",
    #: and a bare size list cannot tell a text model from a vision encoder.
    capability: str = ""
    path: str = ""
    modified: str = ""
    context_length: int = 0
    source: str = ""

    @property
    def size_label(self) -> str:
        return human_size(self.size_bytes) if self.size_bytes else ""

    @property
    def params_label(self) -> str:
        if not self.parameter_count:
            return ""
        if self.parameter_count >= 1:
            return f"{self.parameter_count:.0f}B".replace(".0B", "B")
        return f"{self.parameter_count:g}B"

    def as_row(self) -> dict[str, str]:
        return {
            "name": self.name,
            "provider": self.provider,
            "size": self.size_label,
            "params": self.params_label,
            "quant": self.quantization,
            "family": self.family,
            "source": self.source,
        }


@dataclass
class ProviderInfo:
    """A runtime that can serve models, local or remote."""

    id: str
    label: str
    kind: ProviderKind
    flavour: LocalFlavour = LocalFlavour.UNKNOWN
    base_url: str = ""
    api_key_env: str = ""
    binary: str = ""
    binary_path: str = ""
    version: str = ""
    detail: str = ""
    healthy: bool = False
    latency_ms: float = 0.0
    checked_at: float = 0.0
    models: list[ModelInfo] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)

    @property
    def scope_label(self) -> str:
        return "local" if self.kind is ProviderKind.LOCAL else "distant"

    @property
    def status_label(self) -> str:
        if not self.checked_at:
            return "non testé"
        return "actif" if self.healthy else "injoignable"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind.value,
            "flavour": self.flavour.value,
            "base_url": self.base_url,
            "binary_path": self.binary_path,
            "version": self.version,
            "healthy": self.healthy,
            "latency_ms": round(self.latency_ms, 1),
            "capabilities": list(self.capabilities),
            "models": [m.as_row() for m in self.models],
        }


class ChatStream(Protocol):
    """What every provider must be able to do."""

    def models(self) -> list[ModelInfo]: ...

    def stream(self, model: str, messages: list[dict], **options) -> Iterator[dict]: ...


@dataclass
class StreamChunk:
    """One increment of a streamed answer, whatever produced it."""

    text: str = ""
    done: bool = False
    error: str = ""
    role: str = ""
    meta: dict = field(default_factory=dict)


def human_size(num_bytes: float) -> str:
    """Format a byte count the way a model listing should read."""
    if num_bytes <= 0:
        return ""
    for unit in ("B", "Ko", "Mo", "Go", "To"):
        if num_bytes < 1024 or unit == "To":
            return f"{num_bytes:.0f} {unit}" if unit == "B" else f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return ""


def parse_parameter_count(text: str) -> float:
    """Pull a parameter count, in billions, out of a free-form string.

    Handles what different runtimes actually report: ``qwen3-30b``,
    ``llama3.2:3b``, ``gpt-oss:20b``, and the ``3.0B`` field Ollama returns
    separately. ``M`` and ``K`` suffixes are read too, so a 0.5B model does
    not come back as 500.
    """
    if not text:
        return 0.0
    best = 0.0
    for token in re.findall(r"(\d+(?:\.\d+)?)\s*([bBkKmM])", str(text)):
        try:
            value = float(token[0])
        except ValueError:
            continue
        suffix = token[1].lower()
        if suffix == "b":
            best = max(best, value)
        elif suffix == "m":
            best = max(best, value / 1000.0)
        elif suffix == "k":
            best = max(best, value / 1_000_000.0)
    return best


#: Quantisation markers, longest and most specific first, so that
#: ``Q4_K_M`` is not truncated to ``Q4_K`` by an earlier, shorter pattern.
QUANTIZATION_PATTERNS = (
    r"iq\d[_a-z0-9]*",
    r"q\d+_[a-z0-9_]*",   # q4_k_m, q8_0, q5_k_s
    r"q\d+",              # bare q4, q8
    r"bf16", r"f16", r"fp16", r"fp32", r"f32",
)


def parse_quantization(text: str) -> str:
    """Pull a quantisation label out of a filename or model name.

    Returns the label uppercased, or an empty string when the name carries no
    quantisation information at all.
    """
    if not text:
        return ""
    lowered = str(text).lower()
    for pattern in QUANTIZATION_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            return match.group(0).upper()
    return ""


def now() -> float:
    return time.time()
