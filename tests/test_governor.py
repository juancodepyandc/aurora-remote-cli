"""Turning a memory measurement into a decision.

The tests here pin down one thing above all: asking for quality and getting a
smaller model are different requests, and the second must not be substituted
for the first silently.
"""

import pytest

from aurora_cli.core import agents as agents_mod
from aurora_cli.core import catalog, governor


def _ram(agent, tier: str) -> float:
    return max((a.ram_gb for a in catalog.for_agent(agent) if a.tier == tier),
               default=0.0)


def _machine(total_ram_gb=24.0, free_ram_gb=20.0):
    class M:
        pass
    m = M()
    m.total_ram_gb = total_ram_gb
    m.free_ram_gb = free_ram_gb
    m.disk_free_gb = 500.0
    m.under_pressure = free_ram_gb < total_ram_gb * 0.15
    return m


IMAGE = agents_mod.get("image")


# --- a machine with room -----------------------------------------------------

def test_a_machine_with_enough_room_gets_the_best_tier():
    """Room means room: with 30 GB free the top tier must be offered.

    Comparing against a machine that merely has the total RAM is the wrong
    check, since the top image tier needs 24 GB of working memory and a 24 GB
    machine with 22 GB free cannot actually run it without swapping.
    """
    roomy = _machine(total_ram_gb=64.0, free_ram_gb=30.0)
    decision = governor.decide(IMAGE, roomy)
    assert decision.strategy == "now"
    assert decision.can_run_now
    assert decision.tier == catalog.available_tiers(IMAGE, roomy)[-1] == "max"


# --- asking for the top tier on a busy machine ------------------------------

def test_asking_for_max_waits_rather_than_downgrading():
    """The heart of the change: no silent substitution."""
    decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0), want="max")
    assert decision.strategy == "wait"
    assert decision.tier == "max", "le niveau demandé est conservé"
    assert decision.artifact is None, "rien n'est installé pendant l'attente"
    assert decision.missing_gb > 0


def test_the_wait_says_how_much_is_missing():
    decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0), want="max")
    text = decision.explain()
    assert "manque" in text
    assert "24" in text, "la mémoire requise doit être citée"


def test_the_wait_offers_the_smaller_option_without_choosing_it():
    decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0), want="max")
    assert decision.fallback_tier
    assert decision.fallback_tier != "max"


def test_asking_for_more_than_the_machine_ever_can_hold_is_reported():
    """A wait must be a promise the machine can keep.

    12 GB of total memory can never hold the 3D tiers, whatever is closed, so
    the answer is "not here" and the shortfall is named. Turning this into a
    wait would leave the user watching a countdown for something impossible.
    """
    agent = agents_mod.get("3d")
    machine = _machine(total_ram_gb=12.0, free_ram_gb=8.0)
    decision = governor.decide(agent, machine, want="max")
    assert decision.strategy == "report"
    assert decision.tier == ""
    assert decision.missing_gb > 0
    assert "ne peut pas tourner" in decision.explain()
    assert decision.missing_gb == 4.0, "16 Go demandés moins 12 Go disponibles"


def test_a_wanted_tier_that_could_become_possible_waits():
    """Here waiting is honest: the memory exists, something else holds it.

    The machine is big enough for anything, so holding the request until the
    browser closes is a real offer, not a dead end.
    """
    agent = agents_mod.get("3d")
    machine = _machine(total_ram_gb=64.0, free_ram_gb=8.0)
    decision = governor.decide(agent, machine, want="max")
    assert decision.strategy == "wait"
    assert decision.tier, "un niveau est retenu pour l'attente"
    assert _ram(agent, decision.tier) <= machine.total_ram_gb


# --- no tier asked for: take what fits ---------------------------------------

def test_without_a_wanted_tier_it_installs_what_fits():
    decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0))
    assert decision.strategy == "now"
    assert decision.artifact is not None


def test_the_ceiling_is_still_named_when_a_step_down_happens():
    """Between light and max, a downgrade must still say what was lost."""
    # 9 GB free is under the headroom, so the top tier is out, yet the 8 GB
    # light model still runs. The step down must name the ceiling.
    decision = governor.decide(IMAGE, _machine(free_ram_gb=9.0))
    assert decision.strategy == "now"
    assert decision.tier == "light"
    assert decision.best_tier == "max"
    assert "max" in decision.explain(), "le plafond doit être nommé"
    assert "libres" in decision.explain()


# --- too small at all --------------------------------------------------------

def test_a_machine_too_small_is_never_installed_on():
    """Nothing may be installed that cannot actually start."""
    machine = _machine(total_ram_gb=8.0, free_ram_gb=6.0)
    decision = governor.decide(IMAGE, machine)
    if decision.strategy == "now":
        assert decision.artifact is not None
        assert _ram(IMAGE, decision.tier) <= machine.free_ram_gb
    else:
        assert decision.artifact is None


def test_a_machine_that_can_never_hold_the_job_is_reported():
    decision = governor.decide(IMAGE, _machine(total_ram_gb=4.0, free_ram_gb=3.0))
    assert decision.strategy == "report"
    assert decision.artifact is None


def test_installing_the_smallest_tier_anyway_is_refused():
    """Even the lightest model needs 8 GB; with 6 GB free nothing runs.

    Falling back to it "because it is the smallest" is what produces a machine
    that swaps itself to a standstill the moment the model loads. The answer
    is to hold the job, not to start it.
    """
    decision = governor.decide(IMAGE, _machine(total_ram_gb=24.0, free_ram_gb=6.0))
    assert decision.artifact is None, "rien ne démarre avec 6 Go libres"
    assert decision.missing_gb > 0
    assert "libres" in decision.explain()


# --- report changes nothing --------------------------------------------------

def test_report_never_installs():
    decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0), want="max",
                               strategy="report")
    assert decision.strategy == "report"
    assert decision.artifact is None
    assert decision.tier == ""


def test_report_always_explains():
    decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0), strategy="report")
    assert decision.explain()


# --- naming the applications that hold the memory ----------------------------

def test_the_memory_holders_are_named_by_application():
    rows = governor.memory_holders(limit=3)
    for name, gb in rows:
        assert isinstance(name, str) and name
        assert gb > 0
    assert rows == sorted(rows, key=lambda row: -row[1])


def test_system_processes_are_not_blamed():
    names = {name for name, _ in governor.memory_holders(limit=20)}
    assert "launchd" not in names
    assert "WindowServer" not in names


def test_no_holder_is_named_when_memory_is_fine():
    """The expensive process listing is only run when memory is short."""
    roomy = _machine(total_ram_gb=64.0, free_ram_gb=30.0)
    decision = governor.decide(IMAGE, roomy)
    assert decision.strategy == "now"
    assert decision.held_by == [], "pas d'enquête quand la mémoire suffit"


def test_holders_are_named_when_memory_is_short():
    decision = governor.decide(IMAGE, _machine(free_ram_gb=3.0),
                               strategy="report")
    assert decision.strategy == "report"
    assert decision.held_by, "il faut nommer ce qui occupe la mémoire"


# --- waiting -----------------------------------------------------------------

def test_waiting_returns_immediately_when_memory_is_already_there():
    agent = agents_mod.get("audio")
    assert governor.wait_until(agent, "light", machine=_machine(free_ram_gb=20.0),
                               seconds=5) is True


def test_waiting_gives_up_and_returns_false():
    agent = agents_mod.get("3d-texture")
    assert governor.wait_until(agent, "balanced", machine=_machine(free_ram_gb=3.0),
                               seconds=0, poll=0.1) is False


def test_waiting_never_kills_anything(monkeypatch):
    """A wait must not act on the user's applications.

    The only subprocess this module runs is a read-only process listing; a
    kill would be a decision no software should take for the user.
    """
    import subprocess
    seen = []

    def fake_run(argv, *args, **kwargs):
        seen.append(argv)
        raise subprocess.SubprocessError

    monkeypatch.setattr(governor.subprocess, "run", fake_run)
    governor.memory_holders()
    governor.wait_until(agents_mod.get("audio"), "max",
                        machine=_machine(free_ram_gb=1.0), seconds=0, poll=0.1)
    for argv in seen:
        assert not any(part in ("kill", "killall", "pkill", "terminate")
                       for part in argv), argv


# --- strategy plumbing -------------------------------------------------------

def test_every_strategy_produces_a_decision():
    for strategy in ("auto", "now", "wait", "report"):
        decision = governor.decide(IMAGE, _machine(free_ram_gb=12.0),
                                   want="max", strategy=strategy)
        assert decision.strategy in ("now", "wait", "report")
        assert decision.explain() or strategy == "wait"
