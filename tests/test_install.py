"""Cross-platform install: detection, step order, verification, reporting.

The runner is injected, so these tests never create a virtualenv or run pip.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.install import (
    Installer,
    Report,
    StepResult,
    Target,
    detect_target,
    load_policy,
    policy_file,
    python_version_ok,
)


@pytest.fixture
def target(tmp_path):
    return Target(system="linux", root=tmp_path / "proj", venv=tmp_path / "proj" / ".venv",
                  scripts=tmp_path / "proj" / ".venv" / "bin",
                  bindir=tmp_path / "bin")


@pytest.fixture
def policy():
    return load_policy()


@pytest.fixture
def manifest(tmp_path):
    """Every test project declares a version; the installer's own tests need one."""
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.0"\n', encoding="utf-8")
    return tmp_path


class FakeRunner:
    """Records commands and answers from a scripted map."""

    def __init__(self, results=None):
        self.calls: list[list[str]] = []
        self.results = results or {}

    def __call__(self, command, *, cwd=None, timeout=1800):
        self.calls.append(command)
        for needle, answer in self.results.items():
            if needle in " ".join(command):
                return answer
        return 0, "ok"


def make(target, policy, runner=None):
    return Installer(target=target, policy=policy, project_root=target.root,
                    runner=runner or FakeRunner())


class TestPolicy:
    def test_packaged_policy_is_readable(self):
        assert policy_file().is_file()
        assert policy_file().suffix == ".toml"
        assert load_policy()["environment"]["min_python"]

    def test_missing_policy_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.setenv("JOBIA_INSTALL_POLICY", str(tmp_path / "absent.toml"))
        with pytest.raises(FileNotFoundError):
            load_policy()


class TestDetection:
    def test_target_is_derived_from_the_running_interpreter(self, tmp_path):
        detected = detect_target(tmp_path)
        assert detected.root == tmp_path
        assert detected.python.suffix in ("", ".exe")

    @pytest.mark.parametrize("current,minimum,expected", [
        ((3, 10), "3.10", True),
        ((3, 11), "3.10", True),
        ((3, 9), "3.10", False),
        ((3, 13), "3.13", True),
    ])
    def test_version_comparison(self, current, minimum, expected):
        assert python_version_ok(current, minimum) is expected


class TestSteps:
    def test_python_step_refuses_an_interpreter_that_is_too_old(self, target, policy, monkeypatch):
        installer = make(target, policy)
        monkeypatch.setattr(installer, "check_python", lambda: StepResult("python", "failed", "trop vieux"))
        assert installer.install().steps[0].status == "failed"

    def test_failure_stops_the_run_so_the_real_cause_is_not_buried(self, target, policy, manifest):
        runner = FakeRunner({"venv": (1, "venv creation refused")})
        installer = make(target, policy, runner)
        installer.project_root = manifest
        report = installer.install()
        assert not report.ok
        ids = [s.id for s in report.steps]
        assert ids == ["python", "version", "venv"], f"expected the run to stop at venv, got {ids}"

    def test_venv_creation_is_skipped_when_the_binary_already_exists(self, target, policy):
        target.jobia_bin.parent.mkdir(parents=True)
        target.jobia_bin.write_text("#!/bin/sh\n")
        result = make(target, policy).create_venv()
        assert result.status == "skipped"

    def test_link_repoints_an_existing_link(self, target, policy):
        target.jobia_bin.parent.mkdir(parents=True)
        target.jobia_bin.write_text("#!/bin/sh\n")
        target.bindir.mkdir(parents=True)
        stale = target.bindir / "jobia"
        stale.symlink_to(tmp_target := target.bindir / "gone")
        installer = make(target, policy)
        result = installer.link_binary()
        assert result.status == "ok"
        assert stale.resolve() == target.jobia_bin.resolve()

    def test_link_reports_a_failure_instead_of_raising(self, target, policy):
        target.jobia_bin.parent.mkdir(parents=True)
        target.jobia_bin.write_text("#!/bin/sh\n")
        # A directory sitting where the link must go makes symlink() fail.
        (target.bindir / "jobia").mkdir(parents=True)
        result = make(target, policy).link_binary()
        assert result.status == "failed"

    def test_windows_uses_the_venv_scripts_directory_not_a_symlink(self, tmp_path, policy):
        win = Target(system="windows", root=tmp_path, venv=tmp_path / ".venv",
                     scripts=tmp_path / ".venv" / "Scripts", bindir=tmp_path / ".venv" / "Scripts")
        win.scripts.mkdir(parents=True)
        (win.scripts / "jobia.exe").write_text("binary")
        assert Installer(target=win, policy=policy, runner=FakeRunner()).link_binary().status == "ok"

    def test_verify_requires_the_binary_to_answer(self, target, policy):
        installer = make(target, policy)
        assert installer.verify().status == "failed"

    def test_verify_fails_loudly_when_the_binary_is_broken(self, target, policy):
        target.jobia_bin.parent.mkdir(parents=True)
        target.jobia_bin.write_text("broken")
        runner = FakeRunner({"--version": (2, "traceback")})
        result = make(target, policy, runner).verify()
        assert result.status == "failed" and "traceback" in result.detail


class TestReport:
    def test_report_records_host_and_steps(self, target, policy, manifest):
        # A pre-existing venv lets the whole sequence run, so every step is
        # recorded rather than stopping at the first missing prerequisite.
        target.python.parent.mkdir(parents=True)
        target.python.write_text("#!/bin/sh\n")
        target.jobia_bin.write_text("#!/bin/sh\n")
        installer = make(target, policy)
        installer.project_root = manifest
        report = installer.install()
        data = report.to_dict()
        assert data["host"]["system"]
        assert [s["id"] for s in data["steps"]] == list(Installer.STEPS)
        assert data["finished_at"] >= data["started_at"]

    def test_receipt_is_json_and_reloadable(self, target, policy, tmp_path, manifest):
        target.python.parent.mkdir(parents=True)
        target.python.write_text("#!/bin/sh\n")
        target.jobia_bin.write_text("#!/bin/sh\n")
        installer = make(target, policy)
        installer.project_root = manifest
        installer.install()
        path = installer.write_receipt(tmp_path / "receipt" / "install.json")
        data = json.loads(path.read_text())
        assert data["target"]["system"] == "linux"
        assert isinstance(data["steps"], list) and data["steps"]

    def test_ok_is_false_when_any_step_failed(self):
        assert not Report(steps=[StepResult("a", "ok"), StepResult("b", "failed")]).ok
        assert Report(steps=[StepResult("a", "ok"), StepResult("b", "skipped")]).ok


class TestUpdatePath:
    """An update must be a decision, not an unconditional reinstall."""

    def _installed(self, target, name):
        folder = target.venv / "lib" / "python3.11" / "site-packages" / name
        folder.mkdir(parents=True, exist_ok=True)

    def test_nothing_installed_is_reported_as_install_nothing(self, target, policy, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n', encoding="utf-8")
        installer = make(target, policy)
        installer.project_root = tmp_path
        result = installer.check_version()
        assert result.status == "ok" and "rien d'installé" in result.detail

    def test_matching_version_skips_the_step(self, target, policy, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.0"\n', encoding="utf-8")
        self._installed(target, "jobia_cli-1.2.0.dist-info")
        installer = make(target, policy)
        installer.project_root = tmp_path
        result = installer.check_version()
        assert result.status == "skipped" and "déjà à jour" in result.detail

    def test_a_different_version_requests_a_reinstall(self, target, policy, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n', encoding="utf-8")
        self._installed(target, "jobia_cli-1.2.0.dist-info")
        installer = make(target, policy)
        installer.project_root = tmp_path
        result = installer.check_version()
        assert result.status == "ok" and "1.2.0" in result.detail and "2.0.0" in result.detail
        assert getattr(installer, "force_reinstall", False) is True

    def test_the_version_is_read_without_a_stray_extension(self, target, policy, tmp_path):
        (tmp_path / "pyproject.toml").write_text('[project]\nversion = "2.0.0"\n', encoding="utf-8")
        self._installed(target, "jobia_cli-1.2.0.dist-info")
        from aurora_cli.install import installed_version

        assert installed_version(target) == "1.2.0"

    def test_a_hyphen_in_the_distribution_name_does_not_shift_the_version(self, target):
        self._installed(target, "jobia-some-long-name-1.2.0.dist-info")
        from aurora_cli.install import installed_version

        assert installed_version(target) == "1.2.0"

    def test_version_is_part_of_the_step_order(self):
        assert "version" in Installer.STEPS
