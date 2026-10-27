"""Rendering contracts exercised through the actual prompt-toolkit cell grid."""
import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aurora_cli import config, display, themes, ui
from aurora_cli.core.capabilities import Capabilities
from aurora_cli.tui_design import render_document


class SizedOutput(DummyOutput):
    def __init__(self,width=80,height=32):
        self.width,self.height = width,height
    def get_size(self):
        return Size(rows=self.height,columns=self.width)


def grid(app):
    size = app.app.output.get_size()
    screen = app.app.renderer._last_screen
    return '\n'.join(''.join(screen.data_buffer[y][x].char for x in range(size.columns))
                     for y in range(size.rows))


def caps(width=80,**overrides):
    value = Capabilities('linux',True,'truecolor',True,'none',width,32,True,False)
    return replace(value,**overrides)


@pytest.mark.parametrize('width',[60,80,120,200])
def test_readable_cells_survive_streaming_and_resize(width,monkeypatch):
    monkeypatch.setattr(display,'view',display.Display(theme=themes.get('otter'),caps=caps(width)))
    config.set('server_url','https://username:password@pc-maitre.example/private?token=secret')
    async def scenario():
        output = SizedOutput(width)
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=output)
            app.discover = AsyncMock()
            app.latency_ms,app.connection = 24,'connecté'
            app.transcript = '## Vous\nInspecter le projet\n\n## Aurora\nUne réponse lisible\n\n```python\ndef ready():\n    return True\n```'
            app.partial = 'Lecture en cours'
            app.state = ui.MissionView(status='running',id='mis_live')
            app.event({'type':'tool_start','tool':'read_file'})
            app.refresh_body()
            running = asyncio.create_task(app.run())
            await asyncio.sleep(.08)
            text = grid(app)
            assert 'Vous' in text and 'Aurora' in text and 'def ready()' in text
            assert 'Lecture en cours' in text and 'read_file' in text
            assert 'Session' in text and 'Mission mis_live' in text and '24 ms' in text
            assert 'password' not in text and 'token=' not in text
            assert app.body.window.horizontal_scroll == 0
            output.width = 200 if width==60 else 60
            app.app._on_resize()
            await asyncio.sleep(.08)
            assert 'def ready()' in grid(app) and 'read_file' in grid(app)
            app.app.exit()
            await running
    asyncio.run(scenario())


def test_ascii_input_display_keeps_original_request(monkeypatch):
    monkeypatch.setattr(display,'view',display.Display(theme=themes.get('otter'),caps=caps(unicode=False,color_depth='none')))
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=SizedOutput())
            app.discover = AsyncMock()
            app.prompt.text = 'Vérifie → été'
            app.transcript = '## Réponse\nUn résultat utile.'
            app.refresh_body()
            running = asyncio.create_task(app.run())
            await asyncio.sleep(.08)
            assert grid(app).isascii(), sorted(set(c for c in grid(app) if ord(c)>127))
            assert app.prompt.text == 'Vérifie → été'
            assert all('fg:' not in style and 'bg:' not in style for line in app.body_lexer.lines for style,_ in line)
            app.app.exit()
            await running
    asyncio.run(scenario())


@pytest.mark.parametrize('theme',themes.all_themes(),ids=lambda t:t.id)
def test_markdown_code_and_diff_are_real_text_with_theme_tokens(theme):
    text,lines = render_document('## Résultat\n\n```diff\n-old\n+new\n```',60,theme,caps())
    assert 'Résultat' in text and '-old' in text and '+new' in text
    assert '\x1b' not in text
    assert any(style for line in lines for style,_ in line)
    assert max(map(len,text.splitlines()))<=60


def test_idle_has_no_status_clock_and_theme_preview_rolls_back(monkeypatch):
    monkeypatch.setattr(display,'view',display.Display(theme=themes.get('otter'),caps=caps()))
    config.set('theme','otter')
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=SizedOutput())
            app.discover = AsyncMock()
            running = asyncio.create_task(app.run())
            await asyncio.sleep(.08)
            assert app._clock_handle is None
            picker = asyncio.create_task(app.choose_theme())
            await asyncio.sleep(.02)
            pipe.send_text('\x1b[B')
            await asyncio.sleep(.08)
            preview = display.view.theme.id
            assert preview != 'otter' and config.get('theme')=='otter'
            assert 'aperçu immédiat' in grid(app)
            pipe.send_text('\x1b')
            await asyncio.wait_for(picker,2)
            assert display.view.theme.id=='otter' and config.get('theme')=='otter'
            picker = asyncio.create_task(app.choose_theme())
            await asyncio.sleep(.02)
            app.preview_theme(1)
            pipe.send_text('\r')
            await asyncio.wait_for(picker,2)
            assert display.view.theme.id==preview and config.get('theme')==preview
            assert app._clock_handle is None
            app.app.exit()
            await running
    asyncio.run(scenario())


def test_legacy_cycle_command_does_not_claim_or_run_a_remote_stop(monkeypatch):
    monkeypatch.setattr(ui.subprocess,'run',lambda *a,**k:pytest.fail('No local control command'))
    async def scenario():
        with create_pipe_input() as pipe:
            app = ui.WorkspaceApp(history=[],input=pipe,output=DummyOutput())
            await app.submit('/fin-cycle')
            assert 'Aucun entraînement n’a été modifié' in app.transcript
            assert 'PROGRAMMÉ' not in app.transcript
    asyncio.run(scenario())
