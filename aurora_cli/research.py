"""Research: find engines that might beat what is installed.

The loop in `evolution` is only as good as its candidate source. This module
supplies a real one: it queries the Hugging Face Hub for models matching a
capability profile, discards the ones this machine cannot actually run, and
records what it consulted so a later decision can be audited.

Two deliberate design points:

* The Hub is reached through an injectable client (`HubClient`). Tests use
  `StubHubClient` and never touch the network, and the loop keeps working with
  no network at all.
* The per-capability search profile lives in `policies/research.toml`, not in
  this file. Adding a capability, or changing what "best" means for it, is a
  config edit rather than a code change.
"""
from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from .evolution import Candidate, NullResearcher, TaskSpec, policy_path

_RESEARCH_ENV = "JOBIA_RESEARCH_POLICY"


def research_policy_path() -> Path:
    override = os.environ.get(_RESEARCH_ENV)
    if override:
        return Path(override)
    return policy_path().with_name("research.toml")


def _read_structured(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        import tomli

        return tomli.loads(text)


def load_research_policy() -> dict:
    path = research_policy_path()
    if not path.is_file():
        raise FileNotFoundError(f"research policy not found at {path}")
    return _read_structured(path)


class HubClient(Protocol):
    """The slice of the Hub this module needs. Implemented over HTTP, stubbed in tests."""

    def search(self, *, filter: str, sort: str, limit: int) -> list[dict]: ...

    def size_bytes(self, repo_id: str) -> int | None: ...

    def siblings(self, repo_id: str) -> list[str]: ...


class HuggingFaceHubClient:
    """Real Hub access over the public read-only API.

    Queried anonymously: no token is read, so this cannot leak a credential.
    """

    ENDPOINT = "https://huggingface.co/api/models"

    def __init__(self, timeout: int = 20):
        self.timeout = timeout

    def search(self, *, filter: str, sort: str, limit: int) -> list[dict]:
        url = f"{self.ENDPOINT}?filter={filter}&sort={sort}&direction=-1&limit={limit}"
        with urllib.request.urlopen(url, timeout=self.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, list) else []

    def _model(self, repo_id: str) -> dict:
        # blobs=true is what makes the Hub report per-file sizes; without it the
        # payload has no `size` and a download cannot be bounded.
        with urllib.request.urlopen(f"{self.ENDPOINT}/{repo_id}?blobs=true",
                                    timeout=self.timeout) as response:
            return json.loads(response.read().decode("utf-8"))

    def size_bytes(self, repo_id: str) -> int | None:
        """Sum the declared file sizes, or None if the Hub will not say."""
        try:
            meta = self._model(repo_id)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return None
        total = 0
        known = False
        for sibling in meta.get("siblings") or []:
            size = sibling.get("size")
            if isinstance(size, int):
                total += size
                known = True
        return total if known else None

    def siblings(self, repo_id: str) -> list[str]:
        try:
            meta = self._model(repo_id)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return []
        return [s.get("rfilename", "") for s in meta.get("siblings") or []]


class StubHubClient:
    """Fixed catalogue, for tests and offline runs."""

    def __init__(self, catalogue: dict | None = None):
        self.catalogue = catalogue or {}
        self.calls: list[dict] = []

    def search(self, *, filter: str, sort: str, limit: int) -> list[dict]:
        self.calls.append({"filter": filter, "sort": sort, "limit": limit})
        rows = [
            {**entry, "id": repo_id}
            for repo_id, entry in self.catalogue.items()
            if entry.get("filter") == filter
        ]
        return rows[:limit]

    def size_bytes(self, repo_id: str) -> int | None:
        return self.catalogue.get(repo_id, {}).get("size_bytes")

    def siblings(self, repo_id: str) -> list[str]:
        return self.catalogue.get(repo_id, {}).get("files", [])


class HuggingFaceResearcher(NullResearcher):
    """Proposes Hub models for a capability, and explains failures.

    Every rejection is recorded with its reason, so "why did it not try X" is
    always answerable from the decision log.
    """

    def __init__(self, client: HubClient | None = None, *, policy: dict | None = None,
                 free_space_probe=None, notes: list | None = None):
        self.client = client if client is not None else HuggingFaceHubClient()
        self.policy = policy if policy is not None else load_research_policy()
        self.free_space_probe = free_space_probe or (lambda path: shutil.disk_usage(path).free)
        self.notes: list[str] = notes if notes is not None else []

    def _note(self, message: str) -> None:
        self.notes.append(message)

    # -- profile -----------------------------------------------------------
    def profile(self, capability: str) -> dict:
        profiles = self.policy.get("capabilities", {})
        if capability not in profiles:
            raise KeyError(
                f"no research profile for capability {capability!r}; "
                f"add one to {research_policy_path()} instead of guessing filters"
            )
        return profiles[capability]

    def _rejects(self, repo_id: str, profile: dict) -> str | None:
        lowered = repo_id.lower()
        for needle in profile.get("exclude_substrings", []):
            if needle.lower() in lowered:
                return f"excluded by pattern {needle!r}"
        return None

    def _too_big(self, size: int | None, profile: dict, cap: int) -> str | None:
        ceiling_gb = float(profile.get("max_download_gb", 60))
        if size is None:
            # Unknown size is not permission to ignore the ceiling.
            return "size unknown from the Hub, cannot be bounded"
        gib = size / (1 << 30)
        if gib > ceiling_gb:
            return f"{gib:.1f} GiB exceeds the {ceiling_gb:g} GiB policy ceiling"
        if cap and size > cap:
            return f"{gib:.1f} GiB exceeds the {cap / (1 << 30):.1f} GiB of free space"
        return None

    def _adapter_for(self, repo_id: str, profile: dict) -> dict:
        """Name the adapter that can run this model, if one is implemented.

        An empty dict is the honest answer for a model nothing knows how to
        drive yet: the probe then fails loudly and the loop logs it, instead of
        a score being invented for a model that was never run.
        """
        from .adapters import AdapterRegistry
        capability = next((name for name, value in self.policy.get('capabilities', {}).items()
                           if value == profile), '')
        runner, _ = AdapterRegistry().resolve(capability, repo_id)
        if runner and runner.provisioner != 'ollama':
            # An HF repo name is not an Ollama tag. Never execute another
            # architecture merely because its name contains a vendor keyword.
            return {'runner': runner.id, 'command': runner.worker, 'textured': runner.texturing}
        return {}

    def requires_weights(self, repo_id: str, files: list[str], profile: dict) -> str | None:
        """Refuse a repo with no usable weights file, without downloading it."""
        wanted = tuple(profile.get("weight_suffixes", (".safetensors", ".ckpt", ".bin", ".gguf")))
        if any(name.endswith(wanted) for name in files):
            return None
        return "no weight file (.safetensors/.ckpt/.bin/.gguf) declared"

    # -- Researcher protocol ----------------------------------------------
    def candidates(self, task: TaskSpec, limit: int) -> list[Candidate]:
        profile = self.profile(task.capability)
        rows = self.client.search(filter=profile["filter"], sort=profile.get("sort", "downloads"),
                                  limit=limit)
        root = Path(os.environ.get("JOBIA_DATA_DIR") or Path.home())
        try:
            free = int(self.free_space_probe(root))
        except OSError:
            free = 0

        found: list[Candidate] = []
        for row in rows:
            repo_id = row.get("id") or row.get("modelId")
            if not repo_id:
                continue
            reason = self._rejects(repo_id, profile)
            if reason:
                self._note(f"skip {repo_id}: {reason}")
                continue
            size = self.client.size_bytes(repo_id)
            reason = self._too_big(size, profile, free)
            if reason:
                self._note(f"skip {repo_id}: {reason}")
                continue
            files = self.client.siblings(repo_id)
            reason = self.requires_weights(repo_id, files, profile)
            if reason:
                self._note(f"skip {repo_id}: {reason}")
                continue
            found.append(Candidate(
                name=repo_id.split("/")[-1],
                capability=task.capability,
                spec=repo_id,
                source="research",
                install_path=None,
                evidence=self._evidence(repo_id, row, size, len(files), self._adapter_for(repo_id, profile)),
                adapter=self._adapter_for(repo_id, profile),
            ))
        self._note(f"proposed {len(found)} of {len(rows)} rows for {task.capability}")
        return found

    @staticmethod
    def _evidence(repo_id: str, row: dict, size: int | None, file_count: int, adapter: dict) -> str:
        gib = "unknown" if size is None else f"{size / (1 << 30):.1f}GiB"
        runner = adapter.get("runner", "none")
        return (f"Hub {repo_id} downloads={row.get('downloads')} likes={row.get('likes')} "
                f"size={gib} files={file_count} adapter={runner}")

    def explain(self, failure: str, task: TaskSpec) -> str:
        """Explain a failure using this machine's own evidence, not a guess."""
        head = failure.strip().splitlines()[-1][:300] if failure.strip() else "empty failure"
        try:
            profile = self.profile(task.capability)
        except KeyError:
            return f"{head} (no research profile for {task.capability!r})"
        known = [name for name in profile.get("known_failures", {}) if name.lower() in failure.lower()]
        if known:
            return f"{head} — known issue: {profile['known_failures'][known[0]]}"
        return (f"{head} — not a known issue for {task.capability}; "
                f"re-check the adapter for {task.capability} before retrying")
