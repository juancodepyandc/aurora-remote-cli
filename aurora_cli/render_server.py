"""Versioned JSON-lines bridge between a native renderer and Python missions.

Only this module writes protocol stdout. Business logic remains WorkspaceApp;
state messages never contain configured credentials. Both local processes
inherit the trusted launching environment; the renderer executes no user shell.
An unknown protocol version/command yields an error, never an inferred action.
"""
from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict
import json
import os
import queue
import sys
import threading

from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from . import config, display, themes
from .terminal import safe_text
from .ui import INTERACTIVE_COMMANDS, PAGE_LABELS, SLASH_COMMANDS_CATALOG, WorkspaceApp, presentation_command

VERSION = 1
MAX_COMMAND_BYTES = 262144


def theme_record(theme):
    return {'id': theme.id, 'label': theme.label, 'description': theme.description,
            'palette': {**asdict(theme.palette), 'background': theme.palette.surface},
            'spacing': asdict(theme.spacing), 'components': asdict(theme.components),
            'box': theme.box, 'banner': asdict(theme.banner)}


def snapshot(app, *, picker=''):
    """An explicitly bounded view, not the persisted conversation or config."""
    caps = display.view.caps
    clean = lambda value: safe_text(value, unicode=caps.unicode)
    activity = [clean(text).rstrip('\n') for _, text in app.activity_text()]
    return {'version': VERSION, 'type': 'state', 'state': {
        'title': 'Aurora', 'target': clean(app.target()),
        'latency': f'{app.latency_ms:.0f} ms' if app.latency_ms is not None else 'n/d',
        'latency_kind': 'diagnostic HTTP', 'connection': clean(app.connection),
        'health': clean(app.health), 'interactive_commands': sorted(INTERACTIVE_COMMANDS),
        'session': app.session(), 'mission': clean(app.state.id),
        'phase': clean(app.state.phase), 'status': app.state.status, 'busy': app.busy,
        'page': app.page, 'tabs': [{'id': page, 'label': label} for page,label in PAGE_LABELS.items()],
        'welcome':app.is_welcome(),
        'recovery_notices':[clean(text) for text in app.recovery_notices] if app.is_welcome() else [],
        'commands':[{'name':command,'description':clean(description)} for command,_,_,description in SLASH_COMMANDS_CATALOG],
        'body': clean(app.body_source()), 'activity': activity,
        'sidebar': app.sidebar_text().splitlines(),
        'footer': clean('F2 Thèmes · F3 Modèles · F4 Vues · F5 Diagnostic · Ctrl+C Arrêt · Ctrl+Q Sortie'),
        'prompt': clean(app.prompt.text), 'model': clean(config.get('default_model','')),
        'draft_restore': ({'revision':app.draft_restore['revision'],
                           'text':safe_text(app.draft_restore['text'])} if app.draft_restore else None),
        'theme': theme_record(display.view.theme), 'themes': [theme_record(t) for t in themes.all_themes()],
        'models': list(app.models), 'picker': picker, 'unicode': caps.unicode,
        'color_depth': caps.color_depth, 'animation': caps.animation, 'animate': caps.animate,
    }}


class RenderServer:
    def __init__(self, app, output):
        self.app, self.output = app, output
        self.closed = False
        self.picker = ''
        self._preview_original = None
        self._emit_handle = None
        self.background = set()
        app.app.invalidate = self.changed
        app.choose_theme = self.choose_theme
        app.choose_model = self.choose_model

    def write(self, value):
        self.output.write(json.dumps(value,ensure_ascii=False,separators=(',',':'))+'\n')
        self.output.flush()

    def emit(self):
        self._emit_handle = None
        if not self.closed:
            self.write(snapshot(self.app,picker=self.picker))

    def changed(self):
        if not self.closed and self._emit_handle is None:
            self._emit_handle = asyncio.get_running_loop().call_later(.04,self.emit)

    async def choose_theme(self):
        self.picker = 'theme'
        self.changed()

    async def choose_model(self):
        self.picker = 'model'
        self.changed()

    def spawn(self, coroutine, *, mission=False):
        task = asyncio.create_task(coroutine)
        self.background.add(task)
        if mission:
            self.app.task = task
        def finished(completed):
            self.background.discard(completed)
            if not completed.cancelled() and completed.exception():
                self.app.note('Erreur : '+safe_text(completed.exception()))
            self.changed()
        task.add_done_callback(finished)

    async def handle(self, message):
        if not isinstance(message,dict) or message.get('version') != VERSION or message.get('type') != 'command':
            self.write({'version':VERSION,'type':'error','message':'Version ou enveloppe de commande invalide.'})
            return
        command,value = message.get('command'),message.get('value','')
        if not isinstance(value,str) or len(value.encode('utf-8')) > MAX_COMMAND_BYTES:
            self.write({'version':VERSION,'type':'error','message':'Valeur de commande invalide ou trop longue.'})
            return
        if command == 'submit':
            raw = value.strip()
            if not raw:
                return
            if raw == '/quit':
                await self.handle({'version':VERSION,'type':'command','command':'quit'})
                return
            if raw.startswith(('/mode','/permissions')):
                parts = raw.split(maxsplit=1)
                if parts[0] in {'/mode','/permissions'}:
                    field = 'mode' if parts[0]=='/mode' else 'default_permissions'
                    choices = ['auto','local','remote'] if field=='mode' else ['SAFE','STANDARD','AUTONOMOUS','FULL']
                    if len(parts)>1 and parts[1] in choices:
                        config.set(field,parts[1])
                        self.app.note('Prochaines missions · '+field+' : '+parts[1])
                    else:
                        self.app.note(parts[0]+' '+(' | '.join(choices))+'\nRéglage actuel : '+config.get(field,choices[0]))
                    return
            was_busy = self.app.busy
            if was_busy and not presentation_command(raw):
                self.app.note('Une mission est active. Ctrl+C demande son arrêt ; le brouillon est conservé.')
                return
            self.spawn(self.app.submit(raw),mission=not was_busy)
        elif command in {'theme_preview','theme_commit','theme_cancel'}:
            if command == 'theme_cancel':
                if self._preview_original:
                    display.view.use_theme(self._preview_original)
                self._preview_original = None
                self.picker = ''
            else:
                if not themes.is_known(value):
                    self.app.note('Thème inconnu : '+value)
                    return
                if self._preview_original is None:
                    self._preview_original = display.view.theme.id
                display.view.use_theme(value)
                if command == 'theme_commit':
                    config.set('theme',display.view.theme.id)
                    self._preview_original = None
                    self.picker = ''
            self.app.refresh_body()
        elif command in {'model','model_next'}:
            if self.app.busy:
                self.app.note('Le modèle de la mission active reste inchangé.')
                return
            choices = ['',*dict.fromkeys(self.app.models)]
            if command == 'model_next':
                current = config.get('default_model','')
                value = choices[(choices.index(current)+1)%len(choices)] if current in choices else ''
            if value not in choices:
                self.app.note('Modèle absent du dernier inventaire.')
                return
            config.set('default_model',value)
            self.picker = ''
        elif command in {'page','view','view_next'}:
            if command == 'view_next':
                value = self.app.PAGES[(self.app.PAGES.index(self.app.page)+1)%len(self.app.PAGES)]
            if value not in self.app.PAGES:
                self.app.note('Vue inconnue : '+value)
                return
            self.app.page = value
            self.app.home_open = False
            self.app.refresh_body()
        elif command in {'picker_cancel','model_cancel'}:
            self.picker = ''
        elif command == 'doctor':
            self.spawn(self.app.discover())
        elif command == 'stop':
            if self.app.busy:
                self.spawn(self.app.stop())
            else:
                self.app.note('Aucune mission active à arrêter.')
        elif command == 'new':
            if self.app.busy:
                self.app.note('Arrêtez la mission avant de changer de conversation.')
                return
            self.app.new_conversation()
        elif command == 'quit':
            if self.app.busy:
                self.app.note('Une mission est active. Ctrl+C demande son arrêt avant de quitter.')
                return
            self.closed = True
            self.write({'version':VERSION,'type':'exit'})
        else:
            self.write({'version':VERSION,'type':'error','message':'Commande inconnue.'})
            return
        self.changed()

    def close(self):
        self.closed = True
        for handle in (self._emit_handle,self.app._clock_handle,self.app._refresh_handle):
            if handle is not None:
                handle.cancel()
        for task in self.background:
            task.cancel()


async def serve(input_stream, output_stream):
    loop = asyncio.get_running_loop()
    commands = queue.Queue(maxsize=64)
    stopped = threading.Event()
    def read_commands():
        while not stopped.is_set():
            line = input_stream.readline(MAX_COMMAND_BYTES+2)
            while not stopped.is_set():
                try:
                    commands.put(line or None,timeout=.1)
                    break
                except queue.Full:
                    continue
            if not line:
                return
    with create_pipe_input() as terminal_input:
        app = WorkspaceApp(input=terminal_input,output=DummyOutput())
        app.loop = loop
        server = RenderServer(app,output_stream)
        app.show_recovery_notices()
        server.emit()
        server.spawn(app.discover())
        threading.Thread(target=read_commands,name='aurora-render-input',daemon=True).start()
        try:
            while not server.closed:
                line = await asyncio.to_thread(commands.get)
                if line is None:
                    break
                try:
                    if len(line.encode('utf-8'))>MAX_COMMAND_BYTES:
                        server.write({'version':VERSION,'type':'error','message':'Commande trop longue ; liaison fermée.'})
                        break  # Never interpret a trailing fragment as a new action.
                    await server.handle(json.loads(line))
                except (ValueError,TypeError) as exc:
                    server.write({'version':VERSION,'type':'error','message':safe_text(exc)})
        finally:
            stopped.set()
            server.close()


def main():
    from .core import capabilities
    # The native parent passes terminal facts explicitly: its backend uses pipes.
    class ParentTerminal:
        def isatty(self):
            return os.environ.get('JOBIA_RENDER_TTY') == '1'
    caps = capabilities.detect(stream=ParentTerminal())
    selected,_ = themes.resolve(caps=caps,config_theme=config.get('theme',''))
    display.bind(theme=selected,caps=caps)
    protocol_output = sys.stdout
    with redirect_stdout(sys.stderr):
        asyncio.run(serve(sys.stdin,protocol_output))


if __name__ == '__main__':
    main()
