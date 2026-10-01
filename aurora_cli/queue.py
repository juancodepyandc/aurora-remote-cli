"""What to work on next, decided from measurements rather than from me.

A queue that someone has to populate goes empty, and an empty queue is how a
project stops. This one derives its work from what the system can actually
observe about itself:

  * a capability whose models have no runner, ranked by how much traffic it gets;
  * a platform in the matrix that resolves nothing;
  * a configuration excluded by experience but never retried;
  * a fidelity signal that has never been measured.

Each item states the evidence that produced it, so the queue is auditable: you
can always ask why it wants something, and the answer is a number, not an
opinion. `next_task` always returns something when there is an unresolved gap,
which is what keeps the system moving instead of declaring completion.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Task:
    """One piece of work, with the measurement that justifies it."""

    id: str
    kind: str          # adapter | platform | remeasure | investigate
    title: str
    evidence: str
    priority: int = 50   # lower runs first
    blocks: tuple = ()

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title,
                "evidence": self.evidence, "priority": self.priority,
                "blocks": list(self.blocks)}


@dataclass
class Queue:
    """Ordered, deduplicated, and always justified."""

    tasks: list = field(default_factory=list)

    def __iter__(self):
        return iter(self.tasks)

    def __len__(self):
        return len(self.tasks)

    def add(self, task: Task) -> None:
        if any(existing.id == task.id for existing in self.tasks):
            return
        self.tasks.append(task)

    def extend(self, tasks) -> None:
        for task in tasks:
            self.add(task)

    def sorted(self) -> list:
        return sorted(self.tasks, key=lambda t: (t.priority, t.id))

    def next_task(self) -> Task | None:
        return self.sorted()[0] if self.tasks else None

    def blocked(self) -> list:
        """Work that exists but cannot proceed because something is missing."""
        return [t for t in self.sorted() if t.blocks]

    def to_dict(self) -> dict:
        return {"count": len(self.tasks), "next": (self.next_task().to_dict()
                                                   if self.next_task() else None),
                "tasks": [t.to_dict() for t in self.sorted()]}


def from_adapters(registry, capabilities: dict, *, traffic: dict | None = None) -> Queue:
    """Work implied by models that exist but cannot be run.

    A capability whose candidates all lack a runner is the highest-value gap,
    because the research already paid for finding them and the loop cannot use
    them. Traffic, when given, decides which capability matters more.
    """
    traffic = traffic or {}
    queue = Queue()
    for capability, specs in capabilities.items():
        missing = registry.gaps(capability, specs)
        if not missing:
            continue
        queue.add(Task(
            id=f"adapter:{capability}",
            kind="adapter",
            title=f"Écrire un runner pour la capacité {capability}",
            evidence=(f"{len(missing)} modèle(s) trouvé(s) sans runner : "
                      f"{', '.join(sorted(missing)[:3])}"),
            priority=10 + (0 if traffic.get(capability) else 20),
            blocks=tuple(sorted(missing)),
        ))
    return queue


def from_platforms(resolver, hosts) -> Queue:
    """Work implied by declared platforms that cannot resolve anything."""
    queue = Queue()
    for name, host in hosts.items():
        profile = resolver.profile(host)
        if profile["target"] is None:
            queue.add(Task(
                id=f"platform:{name}",
                kind="platform",
                title=f"Déclarer une cible pour {name}",
                evidence=f"aucune cible ne correspond à {profile['host']['key']}",
                priority=30,
            ))
        elif profile["unusable"]:
            queue.add(Task(
                id=f"platform:{name}:{','.join(sorted(profile['unusable']))}",
                kind="platform",
                title=f"Trouver un substitut pour {', '.join(sorted(profile['unusable']))} sur {name}",
                evidence=(f"sur {profile['host']['key']} (cible "
                          f"'{profile['target']}'), ces paquets ne sont pas "
                          f"installables : {', '.join(sorted(profile['unusable']))}"),
                priority=50,
            ))
    return queue


def from_experience(experience, capability: str, spec: str) -> Queue:
    """Configurations that failed enough times to be excluded and never retried."""
    queue = Queue()
    for config in sorted(experience.excluded(capability, spec)):
        strategy = {s.config: s for s in experience.strategies(capability, spec)}.get(config)
        causes = ", ".join(sorted(strategy.causes)) if strategy else ""
        queue.add(Task(
            id=f"remeasure:{capability}:{'|'.join(map(str, config))}",
            kind="remeasure",
            title=f"Réessayer {list(config)} maintenant que le contexte a changé",
            evidence=(f"configuration {list(config)} exclue après des échecs "
                      f"répétés ({causes or 'cause inconnue'})"),
            priority=40,
        ))
    return queue


def build(*, adapters=None, platforms=None, experience=None,
          capability="3d", spec="tencent/Hunyuan3D-2.1",
          capabilities=None, hosts=None, traffic=None) -> Queue:
    """Merge every source into one queue. Later sources never erase earlier ones."""
    queue = Queue()
    if adapters is not None and capabilities:
        queue.extend(from_adapters(adapters, capabilities, traffic=traffic))
    if platforms is not None and hosts:
        queue.extend(from_platforms(platforms, hosts))
    if experience is not None:
        queue.extend(from_experience(experience, capability, spec))
    return queue


def persist(queue: Queue, path: Path) -> Path:
    """Write the queue so the next run does not start from amnesia."""
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(queue.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load(path: Path) -> Queue:
    """Read a persisted queue. A missing or corrupt file is an empty queue, not a crash."""
    import json

    queue = Queue()
    if not path.is_file():
        return queue
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return queue
    for row in payload.get("tasks", []):
        queue.add(Task(id=row["id"], kind=row["kind"], title=row["title"],
                       evidence=row["evidence"], priority=row.get("priority", 50),
                       blocks=tuple(row.get("blocks", ()))))
    return queue
