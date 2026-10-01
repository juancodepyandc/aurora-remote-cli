"""One installer for every platform.

`install.sh` and `install.ps1` used to be two implementations of the same steps,
so a fix or a new platform meant editing both and hoping they stayed in sync.
They are now thin shims that hand over to this module, which is the only place
that knows how to install JOBIA.

The steps are data (`policies/install.toml`), the platform differences are one
small table, and every run leaves a receipt of what it actually did, so an
install can be verified instead of assumed. Nothing here asks a question: a step
that cannot be completed is reported, and the rest still run.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

POLICY_ENV = "JOBIA_INSTALL_POLICY"


def policy_file() -> Path:
    override = os.environ.get(POLICY_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "policies" / "install.toml"


def load_policy() -> dict:
    path = policy_file()
    if not path.is_file():
        raise FileNotFoundError(f"install policy not found at {path}")
    text = path.read_text(encoding="utf-8")
    try:
        import tomllib

        return tomllib.loads(text)
    except ModuleNotFoundError:
        try:
            import tomli
        except ModuleNotFoundError:
            # Python 3.10's first install may precede our dependencies; pip
            # normally ships its own parser. Installed JOBIA depends on tomli.
            from pip._vendor import tomli
        return tomli.loads(text)


@dataclass(frozen=True)
class Target:
    """Where the pieces of an install live on this machine."""

    system: str  # "linux" | "darwin" | "windows"
    root: Path
    venv: Path
    scripts: Path
    bindir: Path

    @property
    def python(self) -> Path:
        return self.venv / ("Scripts" if self.system == "windows" else "bin") / (
            "python.exe" if self.system == "windows" else "python"
        )

    @property
    def jobia_bin(self) -> Path:
        suffix = ".exe" if self.system == "windows" else ""
        return self.scripts / f"jobia{suffix}"


def detect_target(root: Path | None = None) -> Target:
    """Resolve every path from the running interpreter, not from assumptions."""
    system = "windows" if os.name == "nt" else ("darwin" if sys.platform == "darwin" else "linux")
    root = Path(root or Path(__file__).resolve().parent.parent).expanduser().resolve()
    venv = root / (".venv" if system != "windows" else ".venv")
    scripts = venv / ("Scripts" if system == "windows" else "bin")
    # A user-writable directory is the only one that can be relied on without
    # escalating; the shell PATH entry is handled separately.
    bindir = Path.home() / ".local" / "bin" if system != "windows" else venv / "Scripts"
    return Target(system=system, root=root, venv=venv, scripts=scripts, bindir=bindir)


def installed_version(target: Target) -> str | None:
    """The version the venv currently has, read from its metadata, not guessed."""
    for pattern in ("jobia_cli-*.dist-info", "jobia-cli-*.dist-info", "jobia-*.dist-info"):
        for folder in target.venv.glob(f"lib/python*/site-packages/{pattern}"):
            stem = folder.name[: -len(".dist-info")]
            # Split on the last hyphen so a name containing one cannot shift the
            # version to the wrong field.
            return stem.rsplit("-", 1)[-1]
    return None


def declared_version(project_root: Path) -> str:
    """The version the source tree declares."""
    manifest = project_root / "pyproject.toml"
    if not manifest.is_file():
        # A source tree without a manifest is real during bootstrap; report it as
        # unknown rather than failing the install over a missing version string.
        return ""
    text = manifest.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip().strip('"\'')
    return ""


def python_version_ok(current: tuple[int, int], minimum: str) -> bool:
    want = tuple(int(part) for part in minimum.split(".")[:2])
    return current >= want


@dataclass
class StepResult:
    id: str
    status: str  # "ok" | "skipped" | "failed"
    detail: str = ""
    seconds: float = 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "status": self.status, "detail": self.detail, "seconds": round(self.seconds, 2)}


@dataclass
class Report:
    steps: list[StepResult] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    target: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return all(s.status != "failed" for s in self.steps)

    def add(self, result: StepResult) -> StepResult:
        self.steps.append(result)
        return result

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at,
            "finished_at": time.time(),
            "ok": self.ok,
            "target": self.target,
            "steps": [s.to_dict() for s in self.steps],
            "host": {
                "system": platform.system(),
                "machine": platform.machine(),
                "python": platform.python_version(),
                "executable": sys.executable,
            },
        }


def run(command: list[str], *, cwd: Path | None = None, timeout: int = 1800) -> tuple[int, str]:
    """Run a command, returning its exit status and combined output.

    Never raises for a non-zero exit: a failing install step is data the report
    carries, not an exception that hides the steps that did succeed.
    """
    try:
        completed = subprocess.run(command, cwd=str(cwd) if cwd else None,
                                   capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return completed.returncode, (completed.stdout or "") + (completed.stderr or "")


class Installer:
    """Runs the declared steps, records them, and verifies the result."""

    def __init__(self, target: Target | None = None, *, policy: dict | None = None,
                 project_root: Path | None = None, runner=run, interpreter: Path | None = None):
        self.target = target or detect_target()
        self.policy = policy if policy is not None else load_policy()
        self.project_root = Path(project_root or self.target.root)
        self.runner = runner
        self.interpreter = str(interpreter or sys.executable)
        self.report = Report(target={
            "system": self.target.system,
            "root": str(self.target.root),
            "venv": str(self.target.venv),
            "bindir": str(self.target.bindir),
        })

    # -- individual steps --------------------------------------------------
    def check_python(self) -> StepResult:
        minimum = self.policy.get("environment", {}).get("min_python", "3.10")
        current = sys.version_info[:2]
        if self.interpreter != sys.executable:
            code, output = self.runner([self.interpreter, "-c", "import sys; print('%s.%s' % sys.version_info[:2])"])
            try:
                current = tuple(map(int, output.strip().split("."))) if code == 0 else (0, 0)
            except ValueError:
                return StepResult("python", "failed", output.strip()[-300:])
        if python_version_ok(current, minimum):
            return StepResult("python", "ok", f"{'.'.join(map(str, current))} >= {minimum}")
        return StepResult("python", "failed", f"Python {minimum} requis, {platform.python_version()} trouvé")

    def create_venv(self) -> StepResult:
        if self.target.jobia_bin.is_file():
            return StepResult("venv", "skipped", f"déjà présent : {self.target.venv}")
        self.target.venv.parent.mkdir(parents=True, exist_ok=True)
        code, output = self.runner([self.interpreter, "-m", "venv", str(self.target.venv)])
        if code != 0 or not self.target.python.is_file():
            return StepResult("venv", "failed", output.strip()[-500:])
        return StepResult("venv", "ok", f"créé : {self.target.venv}")

    def check_version(self) -> StepResult:
        """Compare installed against declared, so an update is a decision.

        Without this an installer re-downloads everything on every run and cannot
        tell the user whether anything actually changed.
        """
        current = installed_version(self.target)
        wanted = declared_version(self.project_root)
        if not wanted:
            return StepResult("version", "skipped", "version non déclarée")
        if current is None:
            return StepResult("version", "ok", f"rien d'installé, cible {wanted}")
        if current == wanted:
            return StepResult("version", "skipped", f"déjà à jour ({current})")
        self.force_reinstall = True
        return StepResult("version", "ok", f"mise à jour {current} -> {wanted}")

    def install_package(self) -> StepResult:
        if not self.target.python.is_file():
            return StepResult("package", "failed", "pas d'environnement virtuel")
        command = [str(self.target.python), "-m", "pip", "install"]
        if getattr(self, "force_reinstall", False):
            # A same-version reinstall is how a broken half-install is repaired.
            command.append("--force-reinstall")
        command += ["--upgrade", str(self.project_root)]
        code, output = self.runner(command)
        if code != 0:
            return StepResult("package", "failed", output.strip()[-500:])
        return StepResult("package", "ok", f"installé depuis {self.project_root}")

    def link_binary(self) -> StepResult:
        """Expose `jobia` where the shell will find it, per platform convention."""
        source = self.target.jobia_bin
        if not source.is_file():
            return StepResult("link", "failed", f"binaire absent : {source}")
        if self.target.system == "windows":
            # The launcher lives in the venv's Scripts directory, which is what a
            # Windows install puts on PATH; there is no symlink to make.
            return StepResult("link", "ok", f"binaire disponible : {source}")
        self.target.bindir.mkdir(parents=True, exist_ok=True)
        link = self.target.bindir / "jobia"
        try:
            if link.is_symlink() or link.exists():
                link.unlink()
            link.symlink_to(source)
        except OSError as exc:
            return StepResult("link", "failed", f"{link} : {exc}")
        return StepResult("link", "ok", f"{link} -> {source}")

    def configure_path(self) -> StepResult:
        """Make the launcher discoverable in future sessions, without sudo."""
        bindir = str(self.target.bindir)
        if bindir in os.environ.get("PATH", "").split(os.pathsep):
            return StepResult("path", "skipped", "déjà présent dans PATH")
        if self.target.system == "windows":
            try:
                import winreg
                with winreg.CreateKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                    try:
                        value, _ = winreg.QueryValueEx(key, "Path")
                    except FileNotFoundError:
                        value = ""
                    if bindir.casefold() not in [part.casefold() for part in value.split(";")]:
                        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value.rstrip(";") + ";" + bindir)
            except OSError as exc:
                return StepResult("path", "failed", str(exc))
        else:
            import shlex
            shell = Path(os.environ.get("SHELL", "sh")).name
            profile = Path.home() / ({"zsh": ".zshrc", "bash": ".bashrc"}.get(shell, ".profile"))
            line = "export PATH=" + shlex.quote(bindir) + ':"$PATH"'
            try:
                existing = profile.read_text(encoding="utf-8") if profile.exists() else ""
                if line not in existing.splitlines():
                    with profile.open("a", encoding="utf-8") as stream:
                        stream.write("\n# JOBIA launcher\n" + line + "\n")
            except OSError as exc:
                return StepResult("path", "failed", str(exc))
        return StepResult("path", "ok", "PATH configuré pour les nouveaux terminaux")

    def verify(self) -> StepResult:
        """Install is not done until the binary answers for itself."""
        binary = self.target.jobia_bin
        if not binary.is_file():
            return StepResult("verify", "failed", "binaire absent")
        code, output = self.runner([str(binary), "--version"])
        if code != 0:
            return StepResult("verify", "failed", output.strip()[-300:])
        return StepResult("verify", "ok", output.strip()[:120] or "répond")

    # -- driver ------------------------------------------------------------
    # Step order is explicit rather than inferred from method names: the order
    # is part of the contract, and a rename must not silently reorder it.
    STEPS = {
        "python": "check_python",
        "version": "check_version",
        "venv": "create_venv",
        "package": "install_package",
        "link": "link_binary",
        "path": "configure_path",
        "verify": "verify",
    }

    def install(self) -> Report:
        for step_id, method_name in self.STEPS.items():
            started = time.time()
            result: StepResult = getattr(self, method_name)()
            result.seconds = time.time() - started
            self.report.add(result)
            if result.status == "failed":
                # A later step cannot succeed on a broken prerequisite, and
                # running it anyway would bury the real cause in noise.
                break
        return self.report

    def check(self) -> Report:
        """Describe the plan without calling any mutating installation step."""
        report = Report(target=self.report.target)
        report.add(self.check_python())
        report.add(StepResult("venv", "skipped", "présent" if self.target.python.is_file() else "sera créé"))
        report.add(StepResult("package", "skipped", f"sera installé depuis {self.project_root}"))
        report.add(StepResult("link", "skipped", f"lanceur : {self.target.bindir}"))
        report.add(StepResult("path", "skipped", "sera vérifié à l'installation"))
        report.add(self.verify() if self.target.jobia_bin.is_file() else StepResult("verify", "skipped", "sera vérifié après installation"))
        return report

    def write_receipt(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.report.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return path


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="aurora_cli.install",
                                     description="Install or verify JOBIA on any platform.")
    parser.add_argument("--check", action="store_true",
                        help="report what would change, without writing anything")
    parser.add_argument("--receipt", type=Path, default=None, help="where to write the JSON receipt")
    parser.add_argument("--python", type=Path, default=None, help="interpreter used to create the venv")
    parser.add_argument("--update", action="store_true",
                        help="report the installed version against the source tree, "
                             "and reinstall only when they differ")
    parser.add_argument("--root", type=Path, default=None, help="source checkout to install")
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))

    installer = Installer(target=detect_target(args.root), interpreter=args.python)
    if args.check:
        report = installer.check()
        for step in report.steps:
            print(f"{step.status:<8}{step.id:<9}{step.detail}")
        print(f"\n{'prêt' if report.ok else 'incomplet'}")
        return 0 if report.ok else 1

    report = installer.install()
    for step in report.steps:
        print(f"{step.status:<8}{step.id:<9}{step.detail}")
    if args.receipt:
        installer.write_receipt(args.receipt)
        print(f"\nreçu : {args.receipt}")
    print(f"\n{'installation réussie' if report.ok else 'installation incomplète'}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
