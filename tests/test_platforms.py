"""Platform resolution — exercised on hosts this machine is not.

That is the point: the logic has to be right for Windows, musl and Intel Macs
without any of them being present. Every test constructs a Host explicitly, so
the suite proves the matrix rather than the hardware.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aurora_cli.platforms import Host, Resolver, detect, load_matrix, manifest_path


def host(system="darwin", arch="arm64", libc="", gpu="mps", python=(3, 11)):
    return Host(system=system, arch=arch, machine=arch, python=python,
                implementation="CPython", libc=libc, gpu=gpu)


@pytest.fixture
def resolver():
    return Resolver()


class TestManifest:
    def test_the_shipped_matrix_is_readable(self):
        assert manifest_path().is_file()
        assert load_matrix()["target"]

    def test_every_declared_target_is_reachable(self, resolver):
        """An unreachable target is dead weight that looks like coverage."""
        for name, body in resolver.matrix["target"].items():
            synthetic = host(system=body["system"], arch=body["arch"],
                             libc=body.get("libc", ""), gpu=body.get("gpu", "cpu"))
            assert resolver.target(synthetic) is not None, f"{name} is unreachable"


class TestDetection:
    @pytest.mark.parametrize("machine,expected", [
        ("arm64", "arm64"), ("aarch64", "arm64"), ("armv8l", "arm64"),
        ("x86_64", "x86_64"), ("AMD64", "x86_64"), ("armv7l", "armv7"), ("", "unknown"),
    ])
    def test_architecture_aliases_are_normalised(self, machine, expected):
        assert detect(machine=machine).arch == expected

    def test_the_key_names_libc_when_it_matters(self):
        assert host(system="linux", arch="x86_64", libc="musl").key() == "linux-x86_64-musl"

    def test_musl_and_gnu_are_distinct_hosts(self):
        assert host(system="linux", libc="musl").key() != host(system="linux", libc="gnu").key()


class TestResolution:
    def test_mac_arm64_resolves_the_gpu_stack(self, resolver):
        result = resolver.resolve("torch", host())
        assert result.ok and result.constraint.startswith(">=")

    def test_every_answer_carries_a_reason(self, resolver):
        assert resolver.resolve("torch", host()).reason

    def test_an_undeclared_package_is_refused_by_name(self, resolver):
        result = resolver.resolve("tensorflow", host())
        assert not result.ok and "not declared" in result.reason

    def test_a_package_python_is_too_old_for_is_refused_with_the_versions(self, resolver):
        result = resolver.resolve("torch", host(python=(3, 9)))
        assert not result.ok
        assert "3.9" in result.reason and "needs" in result.reason

    def test_an_intel_mac_resolves_without_claiming_a_gpu(self, resolver):
        result = resolver.resolve("torch", host(system="darwin", arch="x86_64", gpu="cpu"))
        assert result.ok
        assert resolver.target(host(system="darwin", arch="x86_64", gpu="cpu"))["name"] == "macOS Intel"


class TestSpecificity:
    def test_a_narrower_target_wins_over_a_broader_one(self, resolver):
        """Otherwise a musl fix could never override the generic linux rule."""
        matrix = {
            "target": {
                "broad": {"name": "broad", "system": "linux", "arch": "x86_64",
                          "libc": "", "package": {"torch": ">=2.4"}},
                "narrow": {"name": "narrow", "system": "linux", "arch": "x86_64",
                           "libc": "musl", "package": {"torch": ">=3.0"}},
            }
        }
        narrow = Resolver(matrix).resolve("torch", host(system="linux", arch="x86_64", libc="musl"))
        broad = Resolver(matrix).resolve("torch", host(system="linux", arch="x86_64", libc="gnu"))
        assert narrow.constraint == ">=3.0"
        assert broad.constraint == ">=2.4"


class TestMusl:
    def test_musl_declares_no_torch_rather_than_pretending(self, resolver):
        result = resolver.resolve("torch", host(system="linux", arch="x86_64", libc="musl"))
        assert not result.ok and "not declared" in result.reason

    def test_musl_still_resolves_the_portable_packages(self, resolver):
        assert resolver.resolve("pillow", host(system="linux", arch="x86_64", libc="musl")).ok


class TestProfile:
    def test_the_profile_reports_unusable_packages(self, resolver):
        # gpu must match the target, and linux-musl declares none, so it is the
        # arch/libc pair that selects the target here.
        profile = resolver.profile(host(system="linux", arch="x86_64", libc="musl", gpu="cpu"))
        assert "torch" in profile["unusable"]
        assert "pillow" not in profile["unusable"]

    def test_the_profile_names_the_target_and_host(self, resolver):
        profile = resolver.profile(host())
        assert profile["target"] and profile["host"]["key"] == "darwin-arm64-unknown"

    def test_the_profile_serialises(self, resolver):
        data = resolver.profile(host())
        assert set(data) == {"host", "target", "packages", "unusable"}


class TestNoSilentFallback:
    def test_an_undeclared_platform_is_reported_not_defaulted(self):
        resolver = Resolver({"target": {"only-mac": {"system": "darwin", "package": {}}}})
        result = resolver.resolve("torch", host(system="plan9", arch="risc"))
        assert not result.ok and "no target declared" in result.reason
