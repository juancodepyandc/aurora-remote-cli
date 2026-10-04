"""Decide between a smaller model now and a bigger one later.

Knowing the machine is busy is not the same as acting on it. A previous
version reported "12 Go libres sur 24" and then proposed a download, which
leaves the user with a model that will swap the moment it starts. The point
here is to turn the measurement into a decision, and to make waiting a real
option rather than a sentence in a warning.

Three strategies, and the user picks by intent:

**Now.** Take the best tier that fits the memory free right now. The answer
arrives immediately and runs at a quality that fits the machine as it is
currently used. This is the default because a user asking for something while
their editor, browser and video call are open wants the thing done, not a
dialogue about memory.

**Wait.** Hold the request until enough memory is free, then take the tier
that was wanted all along. This is the right answer for a long job scheduled
for later, and it is the only honest way to honour "I want the most powerful
one" on a machine that is temporarily busy.

**Report.** Change nothing, explain what the memory is being used for, and
name the applications holding it. Often the real answer is "close the video
call", and no model choice substitutes for that.

None of these ever stops or kills a process. Waiting is passive, and the
decision to close something belongs to the user.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import agents as agents_mod
from . import catalog
from .catalog import Artifact

#: How long a wait is reasonable before it stops being useful. Past this the
#: user has almost certainly forgotten they asked.
DEFAULT_WAIT_SECONDS = 300

#: Polling interval. Short enough to react when an app closes, long enough not
#: to spin a core.
POLL_SECONDS = 5.0

#: Memory to keep aside for the system and the user's own applications. The
#: model is not the only thing that needs to run.
HEADROOM_GB = 2.0


@dataclass
class Decision:
    """What to do about a job given the memory available right now."""

    agent: object
    #: The tier to install now, or "" when the only honest answer is to wait.
    tier: str = ""
    artifact: Artifact | None = None
    #: The tier the user would get if the machine were free.
    best_tier: str = ""
    #: "now", "wait" or "report".
    strategy: str = "now"
    #: Sentences explaining the choice, in the order they matter.
    reasons: list[str] = field(default_factory=list)
    #: Memory the wanted tier needs that is currently missing.
    missing_gb: float = 0.0
    #: Applications holding the memory, biggest first.
    held_by: list[tuple[str, float]] = field(default_factory=list)
    #: The tier that would run now, for the "smaller instead" choice.
    fallback_tier: str = ""

    @property
    def can_run_now(self) -> bool:
        return self.artifact is not None

    def explain(self) -> str:
        return " ".join(self.reasons)


def memory_holders(limit: int = 3) -> list[tuple[str, float]]:
    """The applications using the most resident memory, biggest first.

    Only consulted when the machine is actually short. The portable process
    listing reads resident memory and executable names. The name
    is taken from the executable rather than the full command line, so the
    output is something a person can recognise instead of a wall of arguments.
    """
    import psutil
    rows: list[tuple[str, float]] = []
    try:
        for process in psutil.process_iter(['name','memory_info']):
            try:
                info = process.info
                name = (info.get('name') or '').replace('\\','/').rsplit('/',1)[-1]
                memory = info.get('memory_info')
                if (not name or not memory or memory.rss<=0 or name.casefold() in
                        {'launchd','kernel_task','windowserver','system','registry','system idle process'}):
                    continue
                rows.append((name,memory.rss/1024**3))
            except (psutil.NoSuchProcess,psutil.AccessDenied):
                continue
    except (OSError,psutil.Error):
        pass
    rows.sort(key=lambda row: -row[1])
    # Only applications big enough to explain the shortfall are worth naming.
    return rows[:max(0,limit)]


def _tier_ram(agent, tier: str) -> float:
    return max((a.ram_gb for a in catalog.for_agent(agent) if a.tier == tier),
               default=0.0)


def decide(agent, machine, *, want: str = "", strategy: str = "auto",
           total_ram_gb: float | None = None) -> Decision:
    """Choose a strategy for one job.

    ``want`` is the tier the user asked for, empty meaning "whatever this
    machine can manage". ``strategy`` is "now", "wait" or "report"; "auto"
    decides, and the decision is written down so it can be challenged.

    The free memory is used rather than the total. Total memory answers "can
    this machine ever run it", which is the wrong question when something
    else is already holding the memory.
    """
    tiers = catalog.available_tiers(agent, machine)
    total = total_ram_gb if total_ram_gb is not None else machine.total_ram_gb
    if not tiers:
        # Not even the smallest tier fits, so closing applications will not
        # help. The shortfall is a number because a rounded "it will not run"
        # leaves the user guessing what would be enough.
        return Decision(agent=agent, strategy="report",
                        missing_gb=max(0.0, agent.ram_floor_gb - total),
                        reasons=[
                            f"Cette machine a {total:.0f} Go de RAM, "
                            f"l'agent {agent.label} demande "
                            f"{agent.ram_floor_gb:.0f} Go : il ne peut pas "
                            "tourner ici."])

    best = tiers[-1]
    # The tier that fits right now, allowing for the system and the user's
    # own applications, which are running whether or not this job is.
    budget = max(0.0, machine.free_ram_gb - HEADROOM_GB)
    fits_now = [t for t in tiers if _tier_ram(agent, t) <= budget]

    # A tier is refused only when it cannot run even with nothing else
    # running, not merely when the headroom is gone. The headroom decides
    # between tiers that are possible; it must not make the smallest one
    # unavailable, which would report a 24 GB machine as unable to run an
    # 8 GB model while 9 GB happen to be free.
    # A job is hopeless only when the machine can never hold it. Comparing
    # against what is free right now declared a 64 GB workstation unable to run
    # a 3D model because a browser was holding 14 GB, turning a recoverable
    # wait into a dead end. The total is the honest bound; the free memory only
    # decides which of the possible tiers to take.
    # The memory a tier needs, and whether it can ever be had here.
    runnable = [t for t in tiers if _tier_ram(agent, t) <= machine.free_ram_gb]
    reachable = [t for t in tiers if _tier_ram(agent, t) <= total]
    if not reachable:
        smallest = _tier_ram(agent, tiers[0])
        decision = Decision(agent=agent, strategy="report", best_tier=best,
                            missing_gb=max(0.0, smallest - machine.free_ram_gb),
                            reasons=[
                                f"Cette machine a {machine.total_ram_gb:.0f} Go "
                                f"de RAM mais il n'en reste que "
                                f"{machine.free_ram_gb:.1f} Go libres, et le "
                                f"plus léger des modèles de l'agent "
                                f"{agent.label} demande {smallest:.0f} Go."])
        decision.held_by = memory_holders()
        return decision

    # The safe fallback is the best tier that fits with room to spare. If not
    # even the smallest fits right now but the machine could hold it, the
    # fallback is that smallest tier: it is the one worth waiting for, and the
    # plan must still name something rather than crash on an empty list.
    fallback = ""
    if fits_now:
        fallback = fits_now[-1]
    elif runnable:
        fallback = runnable[-1]
    elif reachable:
        fallback = reachable[0]

    target = want if want in tiers else best
    target_ram = _tier_ram(agent, target)
    # The wanted tier is honoured when it fits with headroom, or when it can
    # still run even if the machine has to give up the headroom.
    can_now = target_ram <= budget
    # A wait is only offered when the wanted tier is genuinely reachable, so
    # that holding the request is a promise the machine can keep.
    waitable = target in reachable

    if not runnable and strategy != "report":
        # The machine could hold this job, but not while whatever is running
        # now keeps running. Since no tier asked for is possible either, the
        # honest answer is to hold the smallest one rather than start a
        # download that cannot run.
        decision = Decision(agent=agent, strategy="wait", tier=reachable[0],
                            best_tier=best, fallback_tier="",
                            missing_gb=max(0.0, _tier_ram(agent, reachable[0])
                                           - machine.free_ram_gb),
                            reasons=[
                                f"Il ne reste que {machine.free_ram_gb:.1f} Go "
                                f"libres sur {total:.0f} Go : aucun modèle de "
                                f"{agent.label} ne peut démarrer dans cet état, "
                                f"même le plus léger ({_tier_ram(agent, reachable[0]):.0f} Go)."])
        decision.held_by = memory_holders()
        return decision

    if strategy == "report":
        decision = Decision(agent=agent, strategy="report", best_tier=best,
                            reasons=[_pressure_sentence(machine)])
        decision.held_by = memory_holders()
        return decision

    if strategy == "wait" or (strategy == "auto" and want and not can_now):
        if not waitable:
            # The wanted tier can never be had here, so waiting would be a
            # promise the machine cannot keep. Say so and stop.
            decision = Decision(agent=agent, strategy="report", tier="",
                                best_tier=best,
                                missing_gb=max(0.0, target_ram - machine.free_ram_gb),
                                reasons=[
                                    f"Le niveau « {target} » de l'agent "
                                    f"{agent.label} a besoin de {target_ram:.0f} "
                                    f"Go et cette machine n'en a que "
                                    f"{total:.0f} Go au total : attendre ne "
                                    "servira à rien."])
            if fallback:
                decision.reasons.append(
                    f"Le niveau « {fallback} » est possible ici.")
            decision.held_by = memory_holders()
            return decision
        missing = max(0.0, target_ram - budget)
        decision = Decision(agent=agent, strategy="wait", tier=target,
                            best_tier=best, missing_gb=missing,
                            fallback_tier=fallback)
        decision.reasons.append(
            f"Tu as demandé le niveau « {target} », qui a besoin de "
            f"{target_ram:.0f} Go, et il n'y a que {machine.free_ram_gb:.0f} Go "
            f"libres.")
        if decision.missing_gb:
            decision.reasons.append(
                f"Il manque environ {decision.missing_gb:.0f} Go. "
                f"Une machine de {total:.0f} Go peut le faire, pas dans cet "
                "état.")
        if fallback:
            decision.reasons.append(
                f"Sinon, le niveau « {fallback} » est disponible tout de "
                "suite.")
        decision.held_by = memory_holders()
        return decision

    tier = target if can_now else fallback
    artifact = catalog.resolve(agent, machine, tier) if tier else None
    decision = Decision(agent=agent, strategy="now", tier=tier,
                        artifact=artifact, best_tier=best,
                        fallback_tier=fallback)
    if tier == best:
        decision.reasons.append(
            f"{machine.free_ram_gb:.0f} Go libres, la qualité maximale "
            f"({target_ram:.0f} Go) tient sans swap.")
    else:
        # Any step down has to say what was given up, or the user cannot tell
        # a considered choice from a silent downgrade.
        decision.reasons.append(
            f"{machine.free_ram_gb:.0f} Go libres sur {total:.0f} Go : le "
            f"niveau « {tier} » ({_tier_ram(agent, tier):.0f} Go) est le plus "
            "haut qui tienne sans swap.")
        top_ram = _tier_ram(agent, best)
        if top_ram > 0:
            decision.reasons.append(
                f"Le niveau « {best} » demanderait {top_ram:.0f} Go ; il en "
                f"manque {max(0.0, top_ram - machine.free_ram_gb):.0f} Go pour "
                "l'obtenir maintenant.")
        if want and want != tier:
            decision.reasons.append(
                f"Le niveau « {want} » demandé n'est pas celui-ci : "
                f"--strategy wait attend que la mémoire se libère.")
    if not fits_now or not can_now:
        decision.held_by = memory_holders()
    return decision


def _pressure_sentence(machine) -> str:
    return (f"Il ne reste que {machine.free_ram_gb:.1f} Go de RAM libre sur "
            f"{machine.total_ram_gb:.0f} Go.")


def wait_until(agent, tier: str, *, machine, seconds: float = DEFAULT_WAIT_SECONDS,
               poll: float = POLL_SECONDS, on_update=None) -> bool:
    """Watch the free memory until the wanted tier fits, or time runs out.

    Returns True when the tier became available. This only reads the machine;
    it never closes anything, because deciding which of the user's
    applications to stop is not a decision software should take for them.

    Memory is re-measured on every pass rather than reusing the figure from
    when the command started, since waiting is pointless if the number never
    changes.
    """
    needed = _tier_ram(agent, tier)
    if needed <= 0:
        return True
    from . import machine as machine_mod
    deadline = time.monotonic() + seconds
    next_report = deadline - seconds
    while True:
        free = machine_mod.profile().free_ram_gb
        if max(0.0, free - HEADROOM_GB) >= needed:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        if on_update and time.monotonic() >= next_report:
            next_report = time.monotonic() + 30
            on_update(free, remaining)
        time.sleep(min(poll, max(0.5, remaining)))
