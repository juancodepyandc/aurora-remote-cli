"""Turn "I want a 3D model" into three concrete, sized options.

The user asked for options rather than a single silent download, and for those
options to fit the machine rather than the author of the code. That means the
sizes here are *budgets*, not catalogue facts: JOBIA asks the provider what the
download actually is when it can, and falls back to a budget the machine can
survive when it cannot.

Every option is a ``(label, ram_needed, description)`` triple. The three
tiers are consistent everywhere, which is the point: a Mac with 16 GB and a
server with 128 GB get the same vocabulary, sized differently.
"""

from __future__ import annotations

from dataclasses import dataclass

from .agents import Agent
from .provision import human


@dataclass(frozen=True)
class Option:
    """One installable choice, with the cost the user cares about."""

    id: str
    label: str
    ram_gb: float
    detail: str
    #: Rough on-disk size budget, used when the provider cannot say.
    bytes: int

    def size_label(self) -> str:
        return human(self.bytes)


def _budget(ram_gb: float, factor: int = 0.55) -> int:
    """Disk budget for a model that must fit in ``ram_gb`` of working memory.

    Q4-ish weights are roughly half the parameter count in gigabytes, so a
    model that runs in 8 GB lands around 4-5 GB on disk. A fixed 2x would
    underestimate the large tiers badly, hence the widening factor.
    """
    return int(ram_gb * (1024 ** 3) * factor)


def options_for(agent: Agent, machine) -> list[Option]:
    """Three tiers for ``agent``, sized against this machine, best first.

    The widest tier is capped at what the machine can hold, so the list never
    offers a download the host would be killed by. A machine under pressure
    loses the top tiers rather than being offered the biggest one anyway.
    """
    budget_ram = machine.total_ram_gb
    if machine.under_pressure:
        # Distress is about free memory, not capacity: cap at what is free
        # plus a margin, and say so through the tiers that remain.
        budget_ram = max(2.0, min(machine.total_ram_gb, machine.free_ram_gb + 2.0))

    tiers = (
        ("max", "Qualité maximale", 3.0, "Meilleur rendu, le plus gourmand."),
        ("balanced", "Équilibré", 1.0, "Bon compromis qualité / vitesse."),
        ("light", "Version light", 0.45, "Le plus rapide, idéal sur machine chargée."),
    )

    out: list[Option] = []
    for tier_id, label, factor, detail in tiers:
        ram = agent.ram_floor_gb * factor
        if ram > budget_ram:
            continue
        out.append(Option(
            id=f"{agent.id}-{tier_id}",
            label=f"{agent.label} — {label}",
            ram_gb=round(ram, 1),
            detail=detail,
            bytes=_budget(ram),
        ))
    if not out:
        out.append(Option(
            id=f"{agent.id}-remote",
            label=f"{agent.label} — à distance",
            ram_gb=0.0,
            detail="Cette machine ne peut pas l'exécuter localement : "
                   "l'agent passera par le service distant.",
            bytes=0,
        ))
    return out


def missing_for(agent: Agent, models, *, require_capability: bool = True) -> list:
    """Models already on disk that can serve ``agent``.

    This is the "recherche entière" step: before offering a download, check
    whether the machine already has something for the job. A user with a
    Hunyuan installed should never be asked to download a second 3D model.
    """
    usable = []
    for model in models:
        if not require_capability:
            usable.append(model)
            continue
        if model.capability and model.capability == agent.capability:
            usable.append(model)
    return usable
