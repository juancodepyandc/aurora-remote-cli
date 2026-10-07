"""Full-screen terminal client with live mission evidence, history and cancellation."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
import json
import os
import re
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

from prompt_toolkit import Application
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, ConditionalContainer
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea

from . import config, display, themes
from .observations import observation_text, terminal_text


@dataclass
class MissionView:
    id: str = ''
    goal: str = ''
    status: str = 'idle'
    phase: str = 'Prêt'
    plan: list = field(default_factory=list)
    criteria: list = field(default_factory=list)
    verified: list = field(default_factory=list)
    evidence: list = field(default_factory=list)
    files: list = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    received_chars: int = 0
    started: float = 0
    last_signal: float = 0
    last_progress: float = 0
    activity: list = field(default_factory=list)

    def record(self, label, style='activity'):
        elapsed = max(0, int(time.monotonic() - self.started)) if self.started else 0
        self.activity.append((style, f'{elapsed//60:02d}:{elapsed%60:02d}  '+plain(label).replace('\n', ' ')[:240]))
        self.activity = self.activity[-5:]

    def consume(self, event):
        kind = event.get('type')
        if kind != 'reconnecting':
            self.last_signal = time.monotonic()
        if kind not in {'heartbeat', 'reconnecting'}:
            self.last_progress = self.last_signal
        if kind == 'step_start':
            self.status, self.phase = 'running',event.get('step','Exécution')
            self.record(('Agent · ' if event.get('worker') else 'Étape · ')+str(self.phase))
        elif kind == 'tool_start':
            self.phase = event.get('tool','Outil')
            self.record('Outil en cours · '+str(self.phase))
        elif kind == 'plan' and not event.get('worker'):
            self.plan,self.criteria = event.get('steps',[]),event.get('criteria',[])
            self.record(f'Plan reçu · {len(self.plan)} étapes')
        elif kind == 'token':
            self.received_chars += len(event.get('content',''))
        elif kind == 'tool_result':
            if not any(e.get('id')==event.get('id') for e in self.evidence):
                self.evidence.append(event)
            self.record(('Outil terminé · ' if event.get('ok') else 'Échec outil · ')+str(event.get('tool','Outil'))+
                        ' · '+observation_text(event, 180),
                        'success' if event.get('ok') else 'failure')
        elif kind in {'environment_observation','command_output','recovery_observation','completion_observation','stagnation_notice',
                      'recovery_start','recovery_proposal','recovery_rejected','review_result'}:
            detail = observation_text(event, 220)
            if detail:
                self.record(detail, 'warning' if kind in {'stagnation_notice','recovery_rejected','review_result'} else 'activity')
        elif kind == 'mission_snapshot':
            self.goal = event.get('request','')
            self.plan,self.criteria = event.get('plan',[]),event.get('criteria',[])
            self.verified,self.evidence = event.get('verified',[]),event.get('evidence',[])
            if event.get('status'):
                self.status = event['status']
                self.phase = 'État distant · '+str(event['status'])
            self.record(f'État reçu · {self.status} · {len(self.plan)} étapes · {len(self.evidence)} preuves')
        elif kind == 'model_metrics':
            self.metrics = event
        elif kind == 'file_received':
            self.files.append(event['path'])
            self.record('Fichier vérifié · '+str(event['path']), 'success')
        elif kind == 'reconnecting':
            self.phase = 'Reconnexion au flux'
            self.record(f"Reconnexion · tentative {event.get('attempt', '?')}", 'warning')
        elif kind == 'mission_complete':
            self.status = 'stopped' if event.get('stopped') else event.get('status','completed')
            self.verified = event.get('verification',{}).get('verified',[])
            self.phase = self.status
            self.record('Mission · '+str(self.status), 'success' if self.status=='completed' else 'warning')
        elif kind in {'error','mission_interrupted'}:
            self.status = 'interrupted' if kind=='mission_interrupted' else 'failed'
            self.phase = self.status
            self.record(event.get('message',event.get('error','Mission interrompue')), 'failure')


def plain(text):
    """Escape terminal control characters from model/tool output."""
    return terminal_text(text)


def retry_payload_valid(pending):
    """Only replay a complete payload with its original deduplication key."""
    if not isinstance(pending, dict):
        return False
    required = {'request', 'workspace', 'permissions', 'model', 'idempotency_key'}
    if not required <= pending.keys() or pending.keys() - (required | {'history', 'session_id'}):
        return False
    if any(not isinstance(pending[key], str) for key in required):
        return False
    if not pending['request'].strip() or not pending['idempotency_key'].strip():
        return False
    if 'session_id' in pending and not isinstance(pending['session_id'], str):
        return False
    history = pending.get('history', [])
    return isinstance(history, list) and all(
        isinstance(m, dict) and m.get('role') in {'user', 'assistant'}
        and isinstance(m.get('content'), str) for m in history
    )


class WorkspaceApp:
    def __init__(self, *, bridge_factory=None, history=None, input=None, output=None):
        from .bridge import Bridge
        from .core.conversation import Conversation
        self.bridge_factory = bridge_factory or Bridge
        self.history = history if history is not None else Conversation.open()
        self.state,self.page,self.task,self.local_process = MissionView(),'conversation',None,None
        self.models,self.health,self.journal = [],'Diagnostic en cours…',''
        self.partial = ''
        self._refresh_handle = None
        self._memory_warning = ''
        self.transcript = ''.join(f"\n{'VOUS' if m['role']=='user' else 'JOBIA'}\n{plain(m['content'])}\n" for m in self.history)
        self.loop = None
        self.body = TextArea(text=self.transcript or 'Bienvenue dans JOBIA.\n\nDécris le résultat que tu veux obtenir.\n/help affiche les commandes et raccourcis.',
                             read_only=True,scrollbar=True,wrap_lines=True)
        self.prompt = TextArea(height=3,multiline=True,wrap_lines=True,
                               completer=WordCompleter(['/help','/missions','/resume','/attach','/retry','/new','/clear','/models','/theme','/mode','/permissions','/quit']))
        self.sidebar = Window(FormattedTextControl(self.sidebar_text),width=34,wrap_lines=True)
        bindings = KeyBindings()
        @bindings.add('enter',filter=has_focus(self.prompt))
        def submit(event):
            raw = self.prompt.text.strip()
            if raw and not self.busy:
                self.prompt.text = ''
                self.task = event.app.create_background_task(self.submit(raw))
            elif raw:
                self.note('Une mission est active. Ctrl+C demande son arrêt ; ton brouillon est conservé.')
        @bindings.add('escape','enter',filter=has_focus(self.prompt))
        def newline(event):
            self.prompt.buffer.insert_text('\n')
        @bindings.add('c-q')
        def quit_app(event):
            if self.busy:
                self.note('Arrête la mission avec Ctrl+C avant de quitter. L’état distant reste conservé.')
            else:
                event.app.exit()
        @bindings.add('c-c')
        def stop(event):
            if self.busy:
                event.app.create_background_task(self.stop())
            else:
                self.prompt.text = ''
        @bindings.add('tab')
        def focus(event):
            event.app.layout.focus_next()
        @bindings.add('s-tab')
        def previous(event):
            event.app.layout.focus_previous()
        @bindings.add('f2')
        def theme_picker(event):
            event.app.create_background_task(self.choose_theme())
        @bindings.add('f3')
        def model_picker(event):
            event.app.create_background_task(self.choose_model())
        @bindings.add('f4')
        def page(event):
            pages = ['conversation','journal','fichiers']
            self.page = pages[(pages.index(self.page)+1)%len(pages)]
            self.refresh_body()
        @bindings.add('f5')
        def refresh(event):
            event.app.create_background_task(self.discover())
        @bindings.add('c-n')
        def fresh(event):
            if not self.busy:
                self.new_conversation()
        header = Window(FormattedTextControl(self.header),height=2,style='class:header')
        sidebar = ConditionalContainer(Frame(self.sidebar,title='Mission · preuves'),Condition(lambda:self.app.output.get_size().columns>=96))
        activity = ConditionalContainer(
            Frame(Window(FormattedTextControl(self.activity_text), height=lambda: 3 if self.app.output.get_size().rows<28 else 7,
                         wrap_lines=False), title='Activité observée'),
            Condition(lambda: self.state.status != 'idle'))
        layout = HSplit([header,activity,VSplit([Frame(self.body,title=lambda:' '+self.page.capitalize()+' '),sidebar],padding=1),
                         Frame(self.prompt,title='Demande · Entrée envoyer · Alt+Entrée nouvelle ligne'),
                         Window(FormattedTextControl(self.footer),height=2,style='class:footer')])
        self.app = Application(layout=Layout(layout,focused_element=self.prompt),key_bindings=bindings,
                               full_screen=True,mouse_support=True,style=self.style(),
                               input=input,output=output,min_redraw_interval=.05,max_render_postpone_time=.1)

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def style(self):
        view = display.view
        if view.caps.color_depth in {'none','ansi'}:
            return Style.from_dict({'header':'bold','footer':'','frame.border':'','text-area':'',
                                    'activity':'', 'success':'bold', 'warning':'bold', 'failure':'bold'})
        palette = view.theme.palette
        return Style.from_dict({'header':f'bold {palette.primary}','footer':palette.muted,
                                'frame.border':palette.border,'frame.label':f'bold {palette.secondary}',
                                'text-area':palette.text,'status':palette.accent,'prompt':palette.primary,
                                'activity':palette.info,'success':palette.success,
                                'warning':palette.warning,'failure':f'bold {palette.error}'})

    def header(self):
        mode = config.get('mode','auto')
        model = config.get('default_model','') or 'sélection du moteur'
        elapsed = f" · {int(time.monotonic()-self.state.started)}s" if self.busy and self.state.started else ''
        frames = '|/-\\'
        glyph = frames[int(time.monotonic()*4)%len(frames)] if self.busy and display.view.caps.animate else '·'
        return [('class:header',f'  JOBIA  {glyph}  {plain(self.state.phase)}{elapsed}\n'),
                ('class:footer',f'  {mode} · {model} · {display.view.theme.id}')]

    def activity_text(self):
        state = self.state
        active = state.status in {'starting', 'running', 'queued'}
        quiet = max(0, int(time.monotonic() - (state.last_progress or state.started))) if state.started else 0
        signal_age = max(0, int(time.monotonic() - state.last_signal)) if state.last_signal else None
        if active and quiet >= 15:
            label = f'Aucune avancée reçue depuis {quiet}s'
            label += ' · connexion vivante' if signal_age is not None and signal_age < 15 and self.local_process is None else ' · en attente de nouvelles'
            style = 'warning'
        else:
            label = {'starting':'Envoi · attente d’acceptation', 'running':'Mission acceptée · suivi du serveur',
                     'completed':'Mission terminée', 'failed':'Mission en échec', 'interrupted':'Mission interrompue',
                     'stopped':'Mission arrêtée'}.get(state.status, state.status)
            style = 'failure' if state.status in {'failed', 'interrupted'} else 'activity'
        rows = 1 if self.app.output.get_size().rows < 28 else 5
        fragments = [(f'class:{style}', '  '+label+'\n'),
                     ('class:footer', f'  {len(state.evidence)} observations · {len(state.files)} fichiers · {state.received_chars} caractères reçus\n')]
        fragments.extend((f'class:{tone}', '  '+text+'\n') for tone,text in state.activity[-rows:])
        return fragments

    def footer(self):
        return [('class:footer','  F2 thème  F3 modèle  F4 vue  F5 diagnostic  Tab navigation  Ctrl+C arrêter  Ctrl+Q quitter\n'),
                ('class:footer','  '+self.health)]

    def sidebar_text(self):
        state = self.state
        lines = [state.id or 'Aucune mission distante',f'État : {state.status}',
                 'Permissions : '+config.get('default_permissions','SAFE'),'','OBJECTIF',plain(state.goal),'','PLAN']
        lines.extend(f'{i+1}. {plain(step)}' for i,step in enumerate(state.plan))
        if not state.plan:
            lines.append('En attente d’un plan enregistré')
        lines.extend(['','CRITÈRES'])
        ok, pending, failed = ('✓ ','○ ','× ') if display.view.caps.unicode else ('+ ','o ','x ')
        lines.extend((ok if c in state.verified else pending)+plain(c) for c in state.criteria)
        lines.extend(['','OBSERVATIONS'])
        lines.extend((ok if e.get('ok') else failed)+e.get('tool','')+' · '+e.get('id','')+
                     '\n  '+observation_text(e, 250) for e in state.evidence[-8:])
        if state.metrics.get('tokens_per_second') is not None:
            lines.extend(['',f"Débit observé : {state.metrics['tokens_per_second']:.1f} tokens/s"])
        lines.append(f'Fichiers reçus : {len(state.files)}')
        return plain('\n'.join(lines))

    def refresh_body(self):
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None
        follow = self.app.layout.has_focus(self.prompt)
        text = self.transcript if self.page=='conversation' else self.journal if self.page=='journal' else '\n'.join(self.state.files) or 'Aucun fichier reçu.'
        if self.page == 'conversation' and self.partial:
            tail = ' · derniers 16 000 caractères' if len(self.partial) == 16000 else ''
            text += '\n\nSORTIE REÇUE · résultat non encore confirmé'+tail+'\n'+self.partial
        self.body.buffer.set_document(Document(text,len(text) if follow else min(self.body.buffer.cursor_position,len(text))),bypass_readonly=True)
        self.app.invalidate()

    def schedule_refresh(self):
        # Coalesce token bursts and journal updates into at most ten body renders/s.
        if self._refresh_handle is None:
            try:
                self._refresh_handle = asyncio.get_running_loop().call_later(.1, self.refresh_body)
            except RuntimeError:
                self.refresh_body()

    def note(self,text):
        self.transcript = (self.transcript+'\n'+plain(text)+'\n')[-200000:]
        self.refresh_body()

    def post(self,callback,*args):
        self.loop.call_soon_threadsafe(callback,*args)

    def event(self,event):
        self.state.consume(event)
        kind = event.get('type')
        if kind not in {'token','heartbeat'}:
            self.journal = (self.journal+'\n'+plain(json.dumps(event,ensure_ascii=False,indent=2)))[-200000:]
        if kind == 'token':
            self.partial = (self.partial + plain(event.get('content','')))[-16000:]
        if kind=='mission_complete':
            self.partial = ''
            if hasattr(self.history,'state'):
                self.history.state.pop('active_mission_id',None)
            result = event.get('result','')
            self.note('\nJOBIA\n'+result)
            if result:
                if not self.history or self.history[-1] != {'role':'assistant','content':result}:
                    self.history.append({'role':'assistant','content':result})
                if hasattr(self.history,'save'):
                    self.history.save()
        elif kind in {'error','mission_interrupted'}:
            self.note(event.get('message',event.get('error','Mission interrompue.'))+'\nÉtat conservé. /missions puis /resume ID pour reprendre.')
        elif kind=='file_received':
            self.note('Fichier reçu et vérifié : '+event['path'])
        elif kind=='plan' and not event.get('worker'):
            self.note('PLAN REÇU\n'+'\n'.join(f'{i+1}. {plain(step)}' for i,step in enumerate(event.get('steps',[]))))
        elif kind in {'environment_observation','tool_result','recovery_observation','completion_observation','stagnation_notice',
                      'recovery_start','recovery_proposal','recovery_rejected','review_result'}:
            detail = observation_text(event)
            if detail:
                label = ('OUTIL · '+str(event.get('tool', ''))+' · '+('réussi' if event.get('ok') else 'échec')) if kind=='tool_result' else 'OBSERVATION'
                self.note(label+'\n'+detail)
        self.schedule_refresh()

    async def discover(self):
        try:
            if config.is_configured():
                def remote():
                    # Doctor checks several services; its response can take
                    # longer than the former five-second read budget.
                    with self.bridge_factory(timeout=30) as client:
                        doctor,models = client.doctor(),client.models()
                    return doctor,models
                doctor,models = await asyncio.to_thread(remote)
                ready = doctor.get('ok') and doctor.get('ready', True)
                unavailable = [check['name'] for check in doctor.get('checks', [])
                               if check.get('name') in {'GPU', 'ComfyUI'} and check.get('ok') is False]
                if doctor.get('gpu_ready') is False and 'GPU' not in unavailable:
                    unavailable.insert(0, 'GPU')
                if unavailable:
                    self.health = 'Pont dégradé · ' + ' · '.join(name + ' indisponible' for name in unavailable)
                elif ready:
                    self.health = 'Pont prêt pour les missions'
                else:
                    self.health = 'Pont indisponible ou dégradé'
                self.health += ' · '+plain(config.resolve_server_url())
                if not ready:
                    reason = doctor.get('error') or '; '.join(
                        str(c.get('name', 'Contrôle'))+': '+str(c.get('detail') or 'échec')
                        for c in doctor.get('checks', []) if c.get('ok') is False
                    )
                    self.health += ' · '+plain(reason or 'jobia doctor --remote')
                if config.get('mode', 'auto') == 'local':
                    self.health += ' · Mode local actif : les demandes sont exécutées sur cet ordinateur'
                self.models = [m.get('name','') for m in models.get('models',[]) if isinstance(m,dict) and m.get('name')]
                selected = config.get('default_model', '') or doctor.get('default_model', '')
                selected_info = next((m for m in doctor.get('models', models.get('models', []))
                                      if isinstance(m, dict) and m.get('name') == selected), {})
                size = selected_info.get('size', 0)
                ram = (doctor.get('hardware') or {}).get('ram_gb')
                warning = ''
                if (doctor.get('gpu_ready') is False and isinstance(size, (int, float))
                        and isinstance(ram, (int, float)) and ram > 0 and size / 1024 ** 3 > ram):
                    warning = (f'Modèle {selected} : {size / 1024 ** 3:.1f} Gio de poids sur disque, '
                               f'{ram:.1f} Gio de RAM, GPU indisponible. '
                               'La mémoire nécessaire au calcul reste à mesurer ; /models permet de choisir le modèle.')
                if warning and warning != self._memory_warning:
                    self.note(warning)
                self._memory_warning = warning
            else:
                from .core.discovery import scan
                result = await asyncio.to_thread(scan,deep=False)
                self.models = [m.name for provider in result.providers if provider.healthy for m in provider.models]
                self.health = f'{len(self.models)} modèle(s) servi(s) localement · jobia connect pour le pont'
        except Exception as exc:
            self.health = 'Diagnostic : '+plain(exc)
        self.app.invalidate()

    async def choose_theme(self):
        name = await radiolist_dialog(title='Thème',text='Choisis le rendu du terminal',
                                     values=[(n,n) for n in themes.theme_ids()]).run_async()
        if name:
            display.view.use_theme(name)
            config.set('theme',name)
            self.app.style = self.style()
            self.app.invalidate()

    async def choose_model(self):
        if self.busy:
            self.note('Le modèle de la mission active reste inchangé.')
            return
        name = await radiolist_dialog(title='Modèle',text='Modèles observés ; leur qualité doit être évaluée sur ta tâche.',
                                     values=[('', 'Réglage par défaut du moteur')]+[(n,n) for n in dict.fromkeys(self.models)]).run_async()
        if name is not None:
            config.set('default_model',name)
            self.app.invalidate()

    def new_conversation(self):
        if hasattr(self.history,'new'):
            self.history = self.history.new()
        else:
            self.history = []
        self.transcript,self.journal = 'Nouvelle conversation. La précédente reste archivée.\n',''
        self.partial = ''
        self.state = MissionView()
        self.refresh_body()

    def _remote(self,raw,*,resume=None,attach=None,pending=None):
        from .transfers import receive_file
        with self.bridge_factory() as client:
            if attach:
                response = client.mission_status(attach)
                if response.get('ok'):
                    response.update(mission_id=attach,cursor=response.get('stream_start_cursor',0))
            elif resume:
                response = client.mission_resume(resume,model=config.get('default_model',''))
            else:
                from urllib.parse import urlsplit
                local_server = urlsplit(client.server_url).hostname in {'127.0.0.1','localhost','::1'}
                payload = pending or {'request':raw,'workspace':str(Path.cwd()) if local_server else '',
                    'permissions':config.get('default_permissions','SAFE'),'model':config.get('default_model',''),
                    'idempotency_key':uuid4().hex}
                response = client.mission_start(**payload)
            mid = response.get('mission_id')
            if mid:
                self.post(self.accepted,mid)
            if not response.get('ok') or not mid:
                raise RuntimeError(response.get('error','Mission non acceptée'))
            snapshot = client.mission_status(mid)
            if snapshot.get('ok'):
                self.post(self.event,{'type':'mission_snapshot',**snapshot})
            terminal = False
            for event in client.mission_stream(mid,last_event_id=response.get('cursor',0)):
                if event.get('type')=='file_transfer':
                    path = receive_file(event,client,Path.cwd())
                    self.post(self.event,{'type':'file_received','path':str(path)})
                else:
                    self.post(self.event,event)
                if event.get('type') in {'mission_complete','error','mission_interrupted'}:
                    terminal = True
            if not terminal:
                raise RuntimeError('Flux fermé sans confirmation terminale ; aucune action relancée')

    def accepted(self,mid):
        self.state.id,self.state.status = mid,'running'
        self.state.phase = 'Mission acceptée'
        self.state.last_progress = time.monotonic()
        self.state.record('Acceptée · '+mid)
        if hasattr(self.history,'state'):
            self.history.state['active_mission_id'] = mid
            self.history.state.pop('pending_request',None)
            self.history.save()
        self.app.invalidate()

    async def submit(self,raw):
        self.loop = asyncio.get_running_loop()
        pending = getattr(self.history,'state',{}).get('pending_request') if raw=='/retry' else None
        if raw=='/retry':
            if pending is None:
                self.note('Aucune demande incertaine à réessayer. /attach ou /resume suit une mission déjà identifiée.')
                return
            if not retry_payload_valid(pending):
                request = pending if isinstance(pending, str) else pending.get('request') if isinstance(pending, dict) else None
                if isinstance(request, str) and request.strip():
                    self.prompt.text = request
                self.note('Reprise enregistrée incomplète : impossible de garantir une relance sans doublon. '
                          'Archive conservée, aucune mission lancée. Vérifie /missions et utilise /attach ID si elle existe. '
                          + ('La demande est remise en saisie ; Entrée créera une nouvelle mission.'
                             if isinstance(request, str) and request.strip() else 'Retape ta demande si elle doit être créée à nouveau.'))
                return
            raw = pending['request']
        if raw.startswith('/'):
            if raw=='/quit':
                self.app.exit()
                return
            if raw=='/new':
                self.new_conversation()
                return
            if raw=='/clear':
                from .core.conversation import Conversation, clear_archives
                try:
                    count, backup = clear_archives()
                    self.history = Conversation.open()
                except (OSError, ValueError, RuntimeError) as exc:
                    self.note('Nettoyage impossible : '+plain(exc))
                    return
                self.transcript, self.journal = '', ''
                self.partial = ''
                self.state, self.page = MissionView(), 'conversation'
                self.prompt.text = ''
                self.note(f'{count} conversation(s) locale(s) effacée(s) pour ce dossier. Nouvelle conversation.'
                          + (f'\nSauvegarde : {backup}' if backup else ''))
                return
            if raw=='/theme':
                await self.choose_theme()
                return
            if raw=='/models':
                await self.choose_model()
                return
            if raw in {'/mode','/permissions'}:
                field = 'mode' if raw=='/mode' else 'default_permissions'
                choices = ['auto','local','remote'] if raw=='/mode' else ['SAFE','STANDARD','AUTONOMOUS','FULL']
                selected = await radiolist_dialog(title=raw[1:],text='Choisis le fonctionnement explicite des prochaines missions.',values=[(v,v) for v in choices]).run_async()
                if selected:
                    config.set(field,selected)
                return
            if raw=='/missions':
                def listing():
                    with self.bridge_factory() as client:
                        return client.missions_list()
                result = await asyncio.to_thread(listing)
                self.note('\n'.join(f"{m['id']} · {m['status']} · {m['request'][:70]}" for m in result.get('missions',[])) or 'Aucune mission enregistrée.')
                return
            if not raw.startswith(('/resume ','/attach ')):
                self.note('/missions · /attach ID · /resume ID · /retry · /models · /theme · /mode · /permissions · /new · /clear · /quit\n/clear : effacer les conversations locales de ce dossier (sauvegardées).\nF4 : journal et fichiers. Ctrl+C : arrêt. Ctrl+N : nouvelle conversation.\nPour connecter le pont : jobia connect dans ton terminal.')
                return
        resume = raw.split(maxsplit=1)[1] if raw.startswith('/resume ') else None
        attach = raw.split(maxsplit=1)[1] if raw.startswith('/attach ') else None
        self.partial = ''
        self.state = MissionView(goal=raw,status='starting',phase='Reprise' if resume else 'Préparation',started=time.monotonic())
        self.state.record('Connexion · attente du serveur')
        self.note('\nVOUS\n'+raw)
        if not resume and not attach and not pending:
            self.history.append({'role':'user','content':raw})
            if hasattr(self.history,'save'):
                self.history.save()
        try:
            if pending or attach or resume or config.get('mode','auto')=='remote' or (config.get('mode','auto')!='local' and config.is_configured()):
                if not attach and not resume and not pending:
                    from urllib.parse import urlsplit
                    local_server = urlsplit(config.resolve_server_url()).hostname in {'127.0.0.1','localhost','::1'}
                    pending = {'request':raw,'workspace':str(Path.cwd()) if local_server else '',
                               'permissions':config.get('default_permissions','SAFE'),
                               'model':config.get('default_model',''),'idempotency_key':uuid4().hex,
                               'history':[{'role':m['role'],'content':m['content'][:3000]+('\n[historique tronqué]' if len(m['content'])>3000 else '')}
                                          for m in self.history[:-1][-10:]]}
                    if hasattr(self.history,'state'):
                        self.history.state['pending_request'] = pending
                        self.history.save()
                await asyncio.to_thread(self._remote,raw,resume=resume,attach=attach,pending=pending)
            else:
                await self.local(raw)
        except Exception as exc:
            self.event({'type':'error','message':str(exc) or type(exc).__name__})
        finally:
            self.app.invalidate()

    async def local(self,raw):
        from .core import locations
        receipt = locations.data_dir()/'ui-runs'/f'{uuid4().hex}.json'
        receipt.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        log = receipt.with_suffix('.log')
        log.touch(mode=0o600,exist_ok=False)
        if hasattr(self.history,'state'):
            self.history.state['last_local_run'] = {'receipt':str(receipt),'log':str(log)}
            self.history.save()
        env = os.environ.copy()
        env.update(PYTHONUNBUFFERED='1',JOBIA_COLOR='never',JOBIA_ANIMATION='none')
        payload = {'request':raw,'history':list(self.history[:-1]),'model':config.get('default_model','')}
        options = dict(stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT,
                       env=env,start_new_session=os.name=='posix')
        self.local_process = await asyncio.create_subprocess_exec(sys.executable,'-m','aurora_cli.ui_worker','--receipt',str(receipt),**options)
        self.state.phase = 'Exécution locale'
        self.state.status = 'running'
        self.state.record('Processus local démarré')
        self.local_process.stdin.write(json.dumps(payload,ensure_ascii=False).encode())
        await self.local_process.stdin.drain()
        self.local_process.stdin.close()
        while chunk := await self.local_process.stdout.read(4096):
            self.journal = (self.journal+plain(chunk.decode('utf-8',errors='replace')))[-200000:]
            self.state.last_progress = time.monotonic()
            self.partial = (self.partial+plain(chunk.decode('utf-8',errors='replace')))[-16000:]
            log.write_text(self.journal,encoding='utf-8')
            self.schedule_refresh()
        code = await self.local_process.wait()
        self.local_process = None
        if not receipt.is_file():
            self.event({'type':'error','message':f'Exécution locale interrompue (code {code}). Journal conservé.'})
            return
        result = json.loads(receipt.read_text(encoding='utf-8'))
        if not result.get('ok'):
            self.event({'type':'error','message':result.get('error','Le résultat local demandé n’est pas vérifié.')})
            return
        self.event({'type':'mission_complete','result':result.get('result',''), 'status':'completed'})

    async def stop(self):
        if self.local_process and self.local_process.returncode is None:
            process = self.local_process
            if os.name=='posix':
                try:
                    os.killpg(process.pid,signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                killer = await asyncio.create_subprocess_exec('taskkill','/PID',str(process.pid),'/T','/F',stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
                await killer.wait()
            try:
                await asyncio.wait_for(process.wait(),5)
            except asyncio.TimeoutError:
                if os.name=='posix':
                    os.killpg(process.pid,signal.SIGKILL)
                else:
                    process.kill()
                await process.wait()
            self.note('Arrêt local demandé ; journal et fichiers conservés.')
        elif self.state.id:
            def request_stop():
                with self.bridge_factory() as client:
                    return client.mission_stop(self.state.id)
            result = await asyncio.to_thread(request_stop)
            self.note('Arrêt demandé au serveur.' if result.get('ok') else result.get('error','Arrêt non confirmé'))
        else:
            self.note('Demande en cours d’acceptation ; attends son identifiant avant de demander l’arrêt.')

    async def run(self):
        self.loop = asyncio.get_running_loop()
        active = getattr(self.history,'state',{}).get('active_mission_id')
        if active:
            self.note('Mission conservée : '+active+' · /attach '+active+' pour suivre son état.')
        if getattr(self.history,'state',{}).get('pending_request'):
            if retry_payload_valid(self.history.state['pending_request']):
                self.note('Une demande a une acceptation incertaine. /retry conserve sa clé pour éviter une double exécution.')
            else:
                self.note('Une demande conservée a un format de reprise incomplet. /retry permet de la récupérer sans la lancer.')
        async def tick():
            while True:
                await asyncio.sleep(.25 if display.view.caps.animate else 1)
                if self.busy:
                    self.app.invalidate()
        def start():
            self.app.create_background_task(self.discover())
            self.app.create_background_task(tick())
        await self.app.run_async(pre_run=start)


def run_ui():
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        from .workspace import dashboard
        dashboard()
        display.hint('Ouvre un terminal interactif pour l’interface ; jobia --help pour les commandes.')
        return
    asyncio.run(WorkspaceApp().run())
