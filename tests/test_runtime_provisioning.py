"""Regressions for real pip failures and runtime selection, without downloads."""
import json
from pathlib import Path
import subprocess

import pytest

from aurora_cli.core import bootstrap
from aurora_cli.core import runtime_provisioning as provisioning


def test_pip_banner_is_not_a_package():
    diagnosis = provisioning.diagnose_pip(
        "ERROR: Failed to build installable wheels for some pyproject.toml based projects\n"
        "╰─> xatlas, PyMCubes\n")
    assert diagnosis.kind == "build"
    assert diagnosis.packages == ("xatlas", "pymcubes")
    assert provisioning.diagnose_pip(
        "ERROR: Failed to build installable wheels for some pyproject.toml based projects"
    ).packages == ()


def test_unavailable_pin_retains_dependency_extras_and_marker():
    lines = ['pymeshlab[extra]==2022.2.post3; python_version >= "3.10"', "torch>=2"]
    changed, repair = provisioning.relax_unavailable_pin(lines, "pymeshlab")
    assert changed == ['pymeshlab[extra]>=2022.2.post3; python_version >= "3.10"', "torch>=2"]
    assert repair["before"] == lines[0]
    assert lines[0].startswith("pymeshlab[extra]==")


@pytest.mark.parametrize("requirement", ["pymeshlab>=2022.2,<2026", "pymeshlab @ https://example.com/a.whl", "unrelated==1"])
def test_non_exact_requirements_are_never_rewritten(requirement):
    assert provisioning.relax_unavailable_pin([requirement], "pymeshlab") == ([requirement], None)


def test_resolves_new_version_without_dropping_required_package(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("pymeshlab==2022.2.post3\ntrimesh>=4\n")
    calls = []

    def run(command, **kwargs):
        candidate = Path(command[command.index("-r") + 1]).read_text()
        calls.append((command, candidate))
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 1, "", "ERROR: No matching distribution found for pymeshlab==2022.2.post3")
        return subprocess.CompletedProcess(command, 0, "Resolved", "")

    monkeypatch.setattr(provisioning.subprocess, "run", run)
    changes = provisioning.install_requirements(Path("python"), requirements)
    assert len(calls) == 3  # original resolution, corrected resolution, installation
    assert "pymeshlab>=2022.2.post3" in calls[-1][1]
    assert "trimesh>=4" in calls[-1][1]
    assert changes[0]["package"] == "pymeshlab"
    history_path = next((tmp_path / "provisioning").glob("*/history.json"))
    assert json.loads(history_path.read_text())["repairs"] == changes
    assert requirements.read_text().startswith("pymeshlab==")
    assert not list(tmp_path.glob(".jobia-*.txt"))


def test_failed_build_tries_wheels_then_compatible_version(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("xatlas==0.0.9\n")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return subprocess.CompletedProcess(command, 1, "", "Failed to build installable wheels for some pyproject.toml based projects\n╰─> xatlas")
        if len(calls) == 2:
            assert "--only-binary" in command
            return subprocess.CompletedProcess(command, 1, "", "No matching distribution found for xatlas==0.0.9")
        assert "xatlas>=0.0.9" in Path(command[command.index("-r") + 1]).read_text()
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(provisioning.subprocess, "run", run)
    assert len(provisioning.install_requirements(Path("python"), requirements)) == 2
    assert len(calls) == 4


def test_repeated_missing_distribution_keeps_requirement_and_stops(tmp_path, monkeypatch):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("required_engine==1\n")
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1, "", "No matching distribution found for required_engine")

    monkeypatch.setattr(provisioning.subprocess, "run", run)
    with pytest.raises(provisioning.ProvisioningError) as error:
        provisioning.install_requirements(Path("python"), requirements)
    assert len(calls) == 2  # changed plan tried once, never identical retries
    assert error.value.log_dir.exists()
    assert "required_engine==1" in requirements.read_text()
    assert "required_engine>=1" in (error.value.log_dir / "requirements-2.txt").read_text()


def test_windows_runtime_python_path(tmp_path):
    assert bootstrap.runtime_python(tmp_path, windows=True) == tmp_path / "Scripts" / "python.exe"
    assert bootstrap.runtime_python(tmp_path, windows=False) == tmp_path / "bin" / "python"


def test_windows_launcher_finds_python_outside_path(monkeypatch):
    executable = "C:/Program Files/Python311/python.exe"
    monkeypatch.setattr(bootstrap.shutil, "which", lambda name: "C:/Windows/py.exe" if name == "py" else executable if name == executable else None)

    def output(command, **kwargs):
        return executable if "-3.11" in command or "-3.12" in command or "-3.10" in command else "3.11"

    monkeypatch.setattr(bootstrap.subprocess, "check_output", output)
    assert bootstrap._compatible_python() == executable


def test_reuses_verified_runtime_before_installing(tmp_path, monkeypatch):
    broken, ready = tmp_path / "broken", tmp_path / "ready"
    monkeypatch.setattr(bootstrap, "_3d_roots", lambda: iter([broken, ready]))
    monkeypatch.setattr(bootstrap, "_probe_3d", lambda root, **kwargs: root == ready)
    monkeypatch.setattr(bootstrap, "run_logged", lambda *a, **kw: pytest.fail("installer must not run"))
    assert bootstrap.ensure_3d_engine() == ready


def test_false_ready_stamp_cannot_bypass_import_probe(tmp_path, monkeypatch):
    root = tmp_path / "port"
    root.mkdir()
    (root / ".jobia-ready").touch()
    assert bootstrap._probe_3d(root, texture=True) is False


def test_healthy_python_engine_skips_pip(monkeypatch):
    root = bootstrap.locations.data_dir() / "engines" / "images"
    python = bootstrap.runtime_python(root)
    python.parent.mkdir(parents=True)
    python.touch()
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(bootstrap.subprocess, "run", run)
    monkeypatch.setattr(bootstrap, "_install_requirements_adaptively", lambda *a: pytest.fail("pip must not run"))
    assert bootstrap.python_engine("images", ["torch", "Pillow"]) == python
    assert len(commands) == 1
    assert "importlib.metadata.version" in commands[0][-1]
    assert "import_module('PIL')" in commands[0][-1]
