"""Execution routing: local first, remote as fallback.

The CLI has two possible homes for a request: a runtime on this machine, or
the remote bridge. The router decides, and explains itself, so the user is
never surprised about where an answer came from.

Policy, in order:

1. explicit ``--provider`` / :envvar:`JOBIA_PROVIDER` / ``provider`` in config
2. ``mode: auto``  -> local if a runtime answers, otherwise remote
3. ``mode: local`` -> local only, fail loudly rather than silently going out
4. ``mode: remote``-> remote only

``auto`` never silently swallows a local failure into a remote request: if
local was selected and then broke, that is reported, because a user who
asked for local to be used has a right to know it was not.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from aurora_cli import brand
from aurora_cli.core import discovery, runtime as runtime_module
from aurora_cli.core.providers import ModelInfo, ProviderInfo, ProviderKind


class Mode(str, Enum):
    AUTO = "auto"
    LOCAL = "local"
    REMOTE = "remote"

    @classmethod
    def parse(cls, raw: str) -> "Mode":
        value = (raw or "").strip().lower()
        for member in cls:
            if member.value == value:
                return member
        raise ValueError(f"Mode inconnu : {raw!r}. Attendu : auto, local, remote.")


@dataclass
class Route:
    """The outcome of a routing decision."""

    mode: Mode
    target: str = ""
    kind: str = ""
    reason: str = ""
    runtime: runtime_module.LocalRuntime | None = None
    provider: ProviderInfo | None = None
    models: list[ModelInfo] = field(default_factory=list)
    local_available: bool = False
    remote_available: bool = False
    fallback_used: bool = False
    warning: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.target)

    def describe(self) -> str:
        if not self.ok:
            return f"aucune route disponible ({self.reason})"
        label = "local" if self.kind == "local" else "distant"
        suffix = f" — repli : {self.warning}" if self.warning else ""
        return f"{label} · {self.target} ({self.reason}){suffix}"


class Router:
    """Chooses where work runs, and keeps the discovery result."""

    def __init__(self, *, mode: str = "", prefer: str = "",
                 result: discovery.ScanResult | None = None):
        self.mode = Mode.parse(mode) if mode else Mode.AUTO
        self.prefer = prefer
        self._result = result
        self._remote_ready = False

    # --- discovery -------------------------------------------------------

    def scan(self, *, deep: bool = False, refresh: bool = False) -> discovery.ScanResult:
        """Run (or reuse) local discovery.

        An injected ``result`` is authoritative: it is never discarded, so a
        caller that already scanned does not pay for a second pass. Call
        :meth:`rescan` when a fresh look is actually wanted.
        """
        if self._result is None or refresh:
            self._result = discovery.scan(deep=deep)
        return self._result

    def rescan(self, *, deep: bool = False) -> discovery.ScanResult:
        """Force a fresh discovery pass, discarding any cached result."""
        return self.scan(deep=deep, refresh=True)

    @property
    def result(self) -> discovery.ScanResult:
        return self.scan()

    def note_remote(self, available: bool) -> None:
        """Tell the router whether the remote bridge answered."""
        self._remote_ready = available

    # --- selection -------------------------------------------------------

    def route(self, model: str = "") -> Route:
        """Decide the execution target, honouring mode and preferences."""
        scan = self.result
        runtime = runtime_module.from_scan(scan.providers, prefer=self.prefer)
        local_models = _models_for(scan, runtime)
        route = Route(
            mode=self.mode,
            local_available=runtime is not None,
            remote_available=self._remote_ready,
            models=local_models,
        )
        if self.prefer and runtime is None and self.prefer:
            route.reason = f"fournisseur demandé « {self.prefer} » introuvable"
            if self.mode is Mode.LOCAL:
                return route
            if not self._remote_ready:
                return route

        if self.mode is Mode.LOCAL:
            if runtime is None:
                route.reason = "aucun runtime local actif"
                return route
            route.target = runtime.info.label
            route.kind = "local"
            route.runtime = runtime
            route.provider = runtime.info
            route.reason = "mode local"
            return route

        if self.mode is Mode.REMOTE:
            if not self._remote_ready:
                route.reason = "mode distant, pont injoignable"
                return route
            route.target = brand.REMOTE_LABEL
            route.kind = "remote"
            route.reason = "mode distant"
            if runtime is not None:
                route.warning = f"{len(local_models)} modèle(s) local/aux ignoré(s)"
            return route

        # auto: local wins when it can serve the request
        if runtime is not None and local_models:
            route.target = runtime.info.label
            route.kind = "local"
            route.runtime = runtime
            route.provider = runtime.info
            route.reason = "auto : runtime local disponible"
            if self.prefer:
                route.reason += f" (sélectionné : {self.prefer})"
            return route

        if self._remote_ready:
            route.target = brand.REMOTE_LABEL
            route.kind = "remote"
            route.reason = "auto : pas de runtime local, repli distant"
            route.fallback_used = True
            return route

        route.reason = "auto : ni runtime local, ni pont distant"
        return route

    def pick_model(self, model: str = "", route: Route | None = None) -> str:
        """Resolve which model id to send, honouring an explicit choice."""
        chosen = route if route is not None else self.route()
        if model:
            return model
        if not chosen.models:
            return ""
        # Prefer something with a parameter count in a usable range.
        ranked = sorted(
            chosen.models,
            key=lambda m: (0 if 3 <= m.parameter_count <= 80 else 1,
                           0 if m.size_bytes else 1,
                           m.name),
        )
        return ranked[0].name


def _models_for(scan: discovery.ScanResult,
                runtime: runtime_module.LocalRuntime | None) -> list[ModelInfo]:
    """Models the selected runtime can actually serve right now."""
    if runtime is not None and runtime.info.models:
        return list(runtime.info.models)
    if runtime is not None:
        return [m for m in scan.loose_models if m.provider == runtime.info.flavour.value]
    return []


def router_from_config(config: dict) -> Router:
    """Build a router from the stored configuration and environment."""
    mode = (os.environ.get(brand.env("mode"), "")
            or str(config.get("mode") or ""))
    prefer = (os.environ.get(brand.env("provider"), "")
              or str(config.get("provider") or ""))
    try:
        return Router(mode=mode, prefer=prefer)
    except ValueError:
        return Router()
