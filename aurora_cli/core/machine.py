"""What this machine can actually run, measured rather than assumed.

Two rules govern this module, both learned from failures on real machines:

* **Nothing is hardcoded.** Memory, VRAM, disk and acceleration are probed
  every call. A threshold table keyed on "M4 Pro" is worthless on the next
  machine and silently wrong on this one today.
* **Distress, not volume.** Enough RAM on paper is not capacity: a machine
  with 64 GB but 1 GB free is more constrained than one with 16 GB free. A
  starving machine needs a smaller model, and every decision downstream hangs
  off this reading, so it is deliberately pessimistic and explains itself.

Apple Silicon reports its GPU as shared unified memory, so "VRAM" there is a
slice of the same pool as RAM and must not be double-counted.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: Free RAM below this share of total is a machine under pressure.
FREE_RAM_WARN = 0.15

#: Free disk below this share of total is a machine that cannot take a model.
FREE_DISK_WARN = 0.10

#: A machine with less total RAM than this cannot run anything useful locally.
MIN_USABLE_RAM_GB = 4.0


def _total_ram_bytes() -> int:
    """Total physical RAM, from the OS rather than a guess."""
    try:
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        return page_size * os.sysconf("SC_AVPHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return 0


def _free_ram_bytes() -> int:
    """Free RAM now, not at boot.

    Deliberately not ``available`` everywhere: on Linux and macOS the kernel
    will happily hand out memory that is really swap-backed, and provisioning
    against that number is how a download starts and then gets OOM-killed.
    """
    total = _total_ram_bytes()
    if total <= 0:
        return 0
    if sys.platform == "darwin":
        # vm_stat pages are free + speculative + purgeable; the rest is active.
        try:
            out = subprocess.run(["vm_stat"], capture_output=True, text=True,
                                 timeout=5).stdout
            page_size = 4096
            for line in out.splitlines():
                if line.startswith("Mach Virtual Memory Statistics"):
                    # The page size is stated on the header line and is 16 KB on
                    # Apple Silicon, not the 4 KB that is usually assumed.
                    if "(page size of" in line:
                        digits = "".join(
                            ch for ch in line.split("page size of")[1]
                            if ch.isdigit())
                        if digits:
                            page_size = int(digits)
                    break
            free = 0
            for line in out.splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                value = value.strip().rstrip(".")
                if not value.isdigit():
                    continue
                # "Pages inactive" is mostly file-backed cache: reclaimable
                # without swapping, but only by writing it back to disk, so it
                # counts as available with a penalty rather than as free.
                # "Pages active" is genuinely in use and is excluded.
                if key.strip() in {"Pages free", "Pages speculative",
                                   "Pages purgeable", "File-backed pages"}:
                    free += int(value) * page_size
                elif key.strip() == "Pages inactive":
                    free += int(value) * page_size // 2
            if free:
                return min(free, total)
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return min(int(line.split()[1]) * 1024, total)
    except (OSError, ValueError, IndexError):
        pass
    # No reliable reading: assume distress so we under-provision rather than
    # start a download that gets the machine killed.
    return int(total * 0.10)


def _nvidia_vram_bytes() -> int:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=8).stdout
        values = [int(line) for line in out.split() if line.strip().isdigit()]
        return max(values) * 1024 * 1024 if values else 0
    except (OSError, subprocess.SubprocessError, ValueError):
        return 0


@dataclass
class MachineProfile:
    """A measurement of the host, plus the verdict derived from it."""

    os_name: str = ""
    os_version: str = ""
    arch: str = ""
    python: str = ""
    cpu_cores: int = 0
    total_ram_gb: float = 0.0
    free_ram_gb: float = 0.0
    free_disk_gb: float = 0.0
    vram_gb: float = 0.0
    unified_memory: bool = False
    accelerator: str = ""
    container: bool = False
    #: Free RAM as a share of total; the number that actually predicts failure.
    ram_pressure: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def size_class(self) -> str:
        """A coarse tier used to pick a default profile.

        Derived from total RAM because that is what stays constant on a given
        machine; free RAM decides distress, not size.
        """
        if self.total_ram_gb >= 96:
            return "large"
        if self.total_ram_gb >= 32:
            return "workstation"
        if self.total_ram_gb >= 12:
            return "mainstream"
        if self.total_ram_gb >= MIN_USABLE_RAM_GB:
            return "light"
        return "minimal"

    @property
    def under_pressure(self) -> bool:
        """True when this machine cannot take a large model right now.

        A machine under pressure must not be offered a 30 GB download even if
        it owns 128 GB: the download competes for the same memory the model
        needs to load, and the failure mode is a silent OOM kill minutes in.
        """
        if self.free_ram_gb < 2.0:
            return True
        if self.ram_pressure < FREE_RAM_WARN:
            return True
        if self.free_disk_gb < 10:
            return True
        return False

    @property
    def usable(self) -> bool:
        return self.total_ram_gb >= MIN_USABLE_RAM_GB and self.size_class != "minimal"

    def reason(self) -> str:
        """One sentence explaining the verdict, for a prompt the user reads."""
        if self.size_class == "minimal":
            return (f"{self.total_ram_gb:.0f} Go de RAM : trop juste pour un "
                    f"modèle local, l'agent restera sur le distant.")
        if self.under_pressure and self.free_ram_gb < 2.0:
            return (f"{self.free_ram_gb:.1f} Go de RAM libre sur "
                    f"{self.total_ram_gb:.0f} Go : machine saturée, je propose "
                    f"des versions light ou je libère de la mémoire d'abord.")
        if self.under_pressure:
            return (f"{self.free_ram_gb:.1f} Go de RAM libre "
                    f"({self.ram_pressure:.0%}) : je propose des versions light.")
        if self.unified_memory:
            return (f"{self.total_ram_gb:.0f} Go de mémoire unifiée, "
                    f"{self.free_ram_gb:.0f} Go libres : confortable pour la "
                    f"qualité maximale.")
        return (f"{self.total_ram_gb:.0f} Go de RAM, {self.free_ram_gb:.0f} Go "
                f"libres, {self.vram_gb:.0f} Go de VRAM : qualité maximale "
                f"possible.")

    def to_dict(self) -> dict:
        data = {k: v for k, v in self.__dict__.items() if k != "notes"}
        data.update({
            "size_class": self.size_class,
            "under_pressure": self.under_pressure,
            "usable": self.usable,
            "notes": list(self.notes),
        })
        return data


def _accelerator() -> tuple[str, bool]:
    """Return ``(label, unified)`` for the best accelerator present."""
    if sys.platform == "darwin" and platform.machine() in {"arm64", "aarch64"}:
        return "Metal (unified)", True
    vram = _nvidia_vram_bytes()
    if vram:
        return f"NVIDIA {vram / (1024 ** 3):.0f} Go", False
    if sys.platform == "darwin":
        return "Metal (partagé)", True
    return "CPU", False


def _container() -> bool:
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup", encoding="utf-8") as handle:
            blob = handle.read()
        return "docker" in blob or "kubepods" in blob or "containerd" in blob
    except OSError:
        return False


def profile(path: str | Path = ".") -> MachineProfile:
    """Measure the host right now.

    Every value is read live. A cached profile would be wrong the moment the
    user closes a browser, and provisioning against a stale reading is how a
    machine gets killed mid-download.
    """
    total = _total_ram_bytes()
    free = _free_ram_bytes()
    accelerator, unified = _accelerator()
    try:
        usage = shutil.disk_usage(str(path))
        free_disk = usage.free / (1024 ** 3)
    except OSError:
        free_disk = 0.0

    prof = MachineProfile(
        os_name=platform.system() or "inconnu",
        os_version=platform.release(),
        arch=platform.machine() or "inconnu",
        python=platform.python_version(),
        cpu_cores=os.cpu_count() or 0,
        total_ram_gb=round(total / (1024 ** 3), 1),
        free_ram_gb=round(free / (1024 ** 3), 1),
        free_disk_gb=round(free_disk, 1),
        vram_gb=round(_nvidia_vram_bytes() / (1024 ** 3), 1) if not unified else 0.0,
        unified_memory=unified,
        accelerator=accelerator,
        container=_container(),
    )
    prof.ram_pressure = round(prof.free_ram_gb / prof.total_ram_gb, 3) if prof.total_ram_gb else 0.0
    if prof.unified_memory:
        prof.notes.append("Mémoire unifiée : la VRAM n'est pas comptée à part.")
    if prof.container:
        prof.notes.append("Environnement conteneurisé : les limites mémoire peuvent être plus basses.")
    if not prof.notes and prof.under_pressure:
        prof.notes.append("Machine sous tension : privilégier des versions light.")
    return prof
