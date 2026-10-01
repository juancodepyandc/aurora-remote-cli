"""Compatibility facade for the shared, resumable 3D pipeline.

All creation entry points use the same reference, runtime and delivery gates.
A structural mesh check is never labelled a visual audit of rendered 3D views.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Callable, TYPE_CHECKING

from .core import locations, pipeline
from .core.request_spec import parse_creation_request

if TYPE_CHECKING:
    from .engine_manager import AutonomousEngineManager, EngineRecord


@dataclass
class PipelineStage:
    name: str
    status: str = "pending"
    output: Path | None = None
    log: str = ""
    duration_s: float = 0.0
    metadata: dict = field(default_factory=dict)


@dataclass
class PipelineResult:
    success: bool
    stages: list[PipelineStage] = field(default_factory=list)
    final_output: Path | None = None
    log: str = ""
    engine_records: list[EngineRecord] = field(default_factory=list)
    checkpoint: Path | None = None


def _stage(stage: pipeline.PipelineStage) -> PipelineStage:
    return PipelineStage(stage.name, stage.status, stage.output, stage.log,
                         stage.duration_s, {"provider": stage.provider,
                                            "error_kind": stage.error_kind})


@contextmanager
def _safe_device(enabled: bool):
    previous = {name: os.environ.get(name) for name in ("JOBIA_DEVICE", "HY3D_BACKEND")}
    try:
        if enabled:
            os.environ.update(JOBIA_DEVICE="cpu", HY3D_BACKEND="cpu")
        yield
    finally:
        if enabled:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


class AutonomousPipeline:
    """Retain the public API while delegating execution to one implementation."""

    def __init__(self, engine_manager: AutonomousEngineManager | None = None):
        # Runtime provisioning belongs to core. Keep an injected manager for
        # callers' lifecycle operations without claiming its registry is proof.
        self.engine_manager = engine_manager
        self.output_root = locations.data_dir() / "outputs" / "3d"

    def run(self, prompt: str, output_dir: Path | None = None, *,
            max_retries: int = 2, safe_mode: bool = False, texture: bool = True,
            progress_cb: Callable[[PipelineStage], None] | None = None) -> PipelineResult:
        request = parse_creation_request(prompt, output_dir)
        callback = (lambda stage: progress_cb(_stage(stage))) if progress_cb else None
        with _safe_device(safe_mode):
            result = pipeline.run_pipeline(
                request.subject, request.output_dir, input_image=request.input_image, texture=texture,
                max_retries=max_retries, progress=callback,
            )
        return PipelineResult(
            success=result.success, stages=[_stage(stage) for stage in result.stages],
            final_output=result.final_output, log=result.log, checkpoint=result.checkpoint,
        )
