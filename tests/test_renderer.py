import sys
from types import SimpleNamespace
from unittest.mock import Mock

import click
import pytest

from aurora_cli import renderer


def test_missing_native_uses_python_only_before_any_launch(monkeypatch):
    monkeypatch.delenv('JOBIA_NATIVE_BIN',raising=False)
    monkeypatch.setattr(renderer,'native_binary',lambda:None)
    assert renderer.launch_native('auto') is False
    assert renderer.launch_native('python') is False
    with pytest.raises(click.ClickException,match='cargo build'):
        renderer.launch_native('native')


def test_native_exit_never_replays_a_request(monkeypatch):
    monkeypatch.setattr(renderer.sys,'platform','win32')
    monkeypatch.setattr(renderer,'native_binary',lambda:'/fixture/aurora-native')
    run = Mock(return_value=SimpleNamespace(returncode=7))
    monkeypatch.setattr(renderer.subprocess,'run',run)
    with pytest.raises(click.ClickException,match='Aucune mission relancée'):
        renderer.launch_native('auto')
    assert run.call_count==1
    args,kwargs = run.call_args
    assert args[0]==['/fixture/aurora-native','--backend',sys.executable]
    assert kwargs['env']['JOBIA_RENDER_TTY']=='1'
    assert kwargs['env']['JOBIA_THEME']


def test_posix_replaces_launcher_without_retaining_an_extra_python(monkeypatch):
    monkeypatch.setattr(renderer.sys,'platform','linux')
    monkeypatch.setattr(renderer,'native_binary',lambda:'/fixture/aurora-native')
    execute = Mock()
    monkeypatch.setattr(renderer.os,'execve',execute)
    monkeypatch.setattr(renderer.subprocess,'run',lambda *a,**k:pytest.fail('No second process launcher'))
    assert renderer.launch_native('native') is True
    assert execute.call_args.args[1]==['/fixture/aurora-native','--backend',sys.executable]


def test_explicit_binary_is_never_silently_ignored(monkeypatch,tmp_path):
    monkeypatch.setenv('JOBIA_NATIVE_BIN',str(tmp_path/'absent'))
    assert renderer.native_binary() is None
    with pytest.raises(click.ClickException):
        renderer.launch_native('auto')
