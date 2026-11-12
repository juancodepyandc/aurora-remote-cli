"""Full-screen terminal client with live mission evidence, history and cancellation."""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

from prompt_toolkit import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_focus
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, ConditionalContainer, FloatContainer, Float, Dimension
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.shortcuts import radiolist_dialog
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea

from . import config, display, themes
from .observations import observation_text, terminal_text
from .mission_domain import domain_label
from .terminal import safe_text, target_label
from prompt_toolkit.layout.margins import ConditionalMargin

from .tui_design import RenderedLexer, TerminalInputProcessor, color_depth, fit, render_document


SLASH_COMMANDS_CATALOG: list[tuple[str, str, str, str]] = [
    ("/home", ">", "ACCUEIL", "Revoir le thème sans effacer la conversation"),
    ("/help", "?", "AIDE", "Commandes et raccourcis"),
    ("/view", ">", "VUES", "Conversation, modules, agents, bilans, journal, fichiers"),
    ("/modules", ">", "MODULES", "Parcours disponibles ; moteurs à vérifier"),
    ("/chat", ">", "MODULE", "Conversation et rédaction"),
    ("/code", ">", "MODULE", "Code, tests et livraison"),
    ("/image", ">", "MODULE", "Création et édition d’images"),
    ("/3d", ">", "MODULE", "Modélisation 3D et fabrication"),
    ("/cyber", ">", "MODULE", "Analyse de sécurité sous permissions"),
    ("/business", ">", "MODULE", "Analyse économique et données"),
    ("/academy", ">", "MODULE", "Apprentissage et sciences"),
    ("/voice", ">", "MODULE", "Transcription et synthèse vocale"),
    ("/skills", ">", "SKILLS", "Voir, chercher, importer : /skills chercher TEXTE · /skills import SOURCE"),
    ("/skill", ">", "SKILLS", "Activer/désactiver un skill pour les demandes : /skill NOM"),
    ("/mcp", ">", "MCP", "Serveurs et outils : /mcp chercher · /mcp import · /mcp install NOM"),
    ("/missions", ">", "SUIVI", "Retrouver les missions du serveur"),
    ("/resume", ">", "REPRISE", "Reprendre un objectif : /resume ID"),
    ("/attach", ">", "SUIVI", "Suivre sans relancer : /attach ID"),
    ("/retry", ">", "REPRISE", "Réessayer l’acceptation avec la même clé"),
    ("/models", ">", "MODÈLE", "Modèles observés (F3)"),
    ("/theme", ">", "THÈME", "Prévisualiser puis choisir un univers (F2)"),
    ("/mode", ">", "EXÉCUTION", "auto, local ou remote"),
    ("/permissions", ">", "ACCÈS", "SAFE, STANDARD, AUTONOMOUS ou FULL"),
    ("/connect", ">", "CONNEXION", "Associer ce PC : code à valider sur Aurora, sans clé"),
    ("/doctor", ">", "CONNEXION", "Diagnostic du pont et des moteurs (F5)"),
    ("/new", ">", "SESSION", "Nouvelle conversation (Ctrl+N)"),
    ("/clear", ">", "SESSION", "Archiver et vider les conversations de ce dossier"),
    ("/avant-apres", ">", "BILANS", "Mesures reçues et limites des comparaisons"),
    ("/gains", ">", "BILANS", "Alias de /avant-apres"),
    ("/agi", ">", "ANALYSE", "Objectif, plan et preuves observés"),
    ("/ontologie", ">", "ANALYSE", "Contraintes de la demande, sans prédiction de résultat"),
    ("/fin-cycle", ">", "CONTRÔLE", "Disponibilité du contrôle d’entraînement distant"),
    ("/quit", ">", "SORTIE", "Quitter (Ctrl+Q)"),
]

STUDIO_MODULES_META = {
    "CHAT": {"title": "Conversation", "capabilities": "Dialogue, rédaction et synthèse"},
    "CODE": {"title": "Code", "capabilities": "Développement, tests et livraison"},
    "IMAGE": {"title": "Image", "capabilities": "Création et édition d’images"},
    "3D": {"title": "3D", "capabilities": "Modélisation, textures et fabrication"},
    "CYBER": {"title": "Sécurité", "capabilities": "Analyse et remédiation sous permissions"},
    "BUSINESS": {"title": "Business", "capabilities": "Analyse économique et données"},
    "LEARNING": {"title": "Académie", "capabilities": "Cours, exercices et sciences"},
    "VOICE": {"title": "Voix", "capabilities": "Transcription et synthèse vocale"},
}

PAGE_LABELS = {
    'conversation': 'Conversation', 'modules': 'Modules', 'agents': 'Agents',
    'skills': 'Skills', 'mcp': 'MCP',
    'avant_apres': 'Bilans', 'journal': 'Journal', 'fichiers': 'Fichiers',
}


class SlashCommandCompleter(Completer):
    """One searchable, theme-independent catalogue for help and completion."""
    VIEW_CHOICES = [(name, '>', label) for name, label in PAGE_LABELS.items()]

    def get_completions(self, document, complete_event):
        stripped = document.text_before_cursor.lstrip()
        if not stripped.startswith('/'):
            return
        if stripped.lower().startswith('/view '):
            sub = stripped[6:].strip().lower()
            for name, _, description in self.VIEW_CHOICES:
                if name.startswith(sub):
                    yield Completion(name, start_position=-len(stripped[6:]),
                                     display=name, display_meta=description)
            return
        if ' ' in stripped:
            return
        for command, _, badge, description in SLASH_COMMANDS_CATALOG:
            if command.startswith(stripped.lower()):
                yield Completion(command, start_position=-len(stripped), display=command,
                                 display_meta=safe_text(description, unicode=display.view.caps.unicode))


@dataclass
class MissionView:
    id: str = ''
    goal: str = ''
    status: str = 'idle'
    phase: str = 'Prêt'
    plan: list = field(default_factory=list)
    server_steps: int | None = None
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
        def collection(value, previous, item_type=str):
            # Snapshots use steps as a counter and plan as a list. Missing or
            # malformed collections cannot erase previously observed details.
            return [item for item in value if isinstance(item, item_type)] if isinstance(value, list) else previous

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
            self.plan = collection(event.get('steps'), self.plan)
            self.criteria = collection(event.get('criteria'), self.criteria)
            self.record(f'Plan reçu · {len(self.plan)} étapes')
        elif kind == 'plan_revision' and not event.get('worker'):
            self.plan = collection(event.get('steps'), self.plan)
            self.record('Étapes adaptées · '+str(event.get('reason','Observations nouvelles')))
        elif kind == 'token':
            if event.get('channel')=='tool_call':
                self.phase = 'Préparation de l’action'
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
            self.id = event.get('id', self.id)
            self.goal = event.get('request',self.goal)
            steps = event.get('steps')
            if type(steps) is int and steps >= 0:
                self.server_steps = steps
            self.plan = collection(event.get('plan'), collection(steps, self.plan))
            self.criteria = collection(event.get('criteria'), self.criteria)
            self.verified = collection(event.get('verified'), self.verified)
            self.evidence = collection(event.get('evidence'), self.evidence, dict)
            if event.get('status'):
                self.status = event['status']
                self.phase = ('État local · ' if event.get('id','').startswith('loc_') else 'État distant · ')+str(event['status'])
            self.record(f'État reçu · {self.status} · {len(self.plan)} étapes · {len(self.evidence)} preuves')
        elif kind in {'context_recovery','context_adjustment'}:
            self.phase = 'Adaptation du contexte'
            self.record(event.get('message','Budget de contexte ajusté avant génération'), 'activity')
        elif kind == 'model_metrics':
            self.metrics = event
        elif kind in {'file_received', 'file_available'}:
            self.files.append(event['path'])
            self.record('Fichier vérifié · '+str(event['path']), 'success')
        elif kind == 'reconnecting':
            self.phase = 'Reconnexion au flux'
            self.record(f"Reconnexion · tentative {event.get('attempt', '?')}", 'warning')
        elif kind in {'permission_request','permission_required'}:
            self.status, self.phase = 'permission', 'Autorisation requise'
            self.record(event.get('message','Confirmation nécessaire sur le PC maître'), 'warning')
        elif kind == 'mission_complete':
            self.status = 'stopped' if event.get('stopped') else event.get('status','completed')
            verification = event.get('verification')
            self.verified = collection(verification.get('verified') if isinstance(verification,dict) else None, self.verified)
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
    if not required <= pending.keys() or pending.keys() - (required | {'history', 'session_id', 'references', 'context_tokens'}):
        return False
    if any(not isinstance(pending[key], str) for key in required):
        return False
    if not pending['request'].strip() or not pending['idempotency_key'].strip():
        return False
    if 'session_id' in pending and not isinstance(pending['session_id'], str):
        return False
    if 'references' in pending and (not isinstance(pending['references'],list) or len(pending['references'])>8 or any(
            not isinstance(ref,dict) or set(ref)!={'id','label'} or
            not all(isinstance(ref[k],str) for k in ('id','label')) for ref in pending['references'])):
        return False
    if 'context_tokens' in pending and (type(pending['context_tokens']) is not int or not 1024<=pending['context_tokens']<=131072):
        return False
    history = pending.get('history', [])
    return isinstance(history, list) and all(
        isinstance(m, dict) and m.get('role') in {'user', 'assistant'}
        and isinstance(m.get('content'), str) for m in history
    )


class HorizonFrame:
    """Quiet rails with a label; character choice follows the active theme."""
    def __init__(self, body, title='', *, height=None):
        def label():
            value = title() if callable(title) else title
            return [('class:frame.label', safe_text(' '+value+' ', unicode=display.view.caps.unicode))]

        def rail():
            return safe_text(display.view.theme.components.rail, unicode=display.view.caps.unicode)[:1] or '|'

        self.container = HSplit([
            Window(FormattedTextControl(label), height=1),
            VSplit([Window(width=1, char=rail, style='class:frame.border'), body,
                    Window(width=1)], padding=1),
        ], height=height)

    def __pt_container__(self):
        return self.container


# Kept for callers of the old public layout helper.
GlassFrame = HorizonFrame


INTERACTIVE_COMMANDS = frozenset({'/help', '/theme', '/view', '/modules',
                                  '/avant-apres', '/gains', '/doctor', '/missions',
                                  '/skills', '/skill', '/mcp', '/connect', '/pair', '/home', '/palette'})


def presentation_command(raw):
    """UI-only commands may run beside a mission, never replace its task."""
    parts = raw.split(maxsplit=1)
    return bool(parts and parts[0].lower() in INTERACTIVE_COMMANDS)


class WorkspaceApp:
    PAGES = list(PAGE_LABELS)

    def __init__(self, *, bridge_factory=None, history=None, input=None, output=None):
        from .bridge import Bridge
        from .core.conversation import Conversation
        self.bridge_factory = bridge_factory or Bridge
        self.history = history if history is not None else Conversation.open()
        self.home_open = True
        self.state,self.page,self.task,self.local_process = MissionView(),'conversation',None,None
        self.active_module = ''
        self.models,self.health,self.journal = [],'Diagnostic en cours…',''
        self.connection = 'non vérifiée'
        self.latency_ms = None
        self._active_target = ''
        self._clock_handle = None
        self._body_key = None
        self._body_source = ''
        self._body_page = ''
        self._theme_selection = None
        self._theme_original = None
        self._theme_future = None
        self.partial = ''
        self._refresh_handle = None
        self._memory_warning = ''
        self._last_connection_warning = ''
        self._recovery_notices_shown = False
        self.draft_restore = None
        # Live connection: transient notices, pairing in progress, backoff.
        self.toast = None
        self.pairing = None
        self._pairing_cancel = None
        self._pairing_attempted = False
        self._backoff = 0
        self.reconnect_at = None
        self.publication_hint = ''
        self.animation_epoch = time.monotonic()
        # Skills and MCP views: last observed inventories, loaded in background.
        self.skill_rows, self.remote_skill_rows, self.mcp_rows, self.remote_mcp_rows = [], [], [], []
        self.integrations_loaded = 0.0
        self.transcript = ''.join(f"\n### {'VOUS' if m['role']=='user' else 'AURORA'}\n\n{plain(m['content'])}\n" for m in self.history)[-200000:]
        self.loop = None
        self.body_lexer = RenderedLexer()
        self.body = TextArea(text='', lexer=self.body_lexer,
                             read_only=True,scrollbar=True,wrap_lines=True)
        self.body.window.right_margins = [
            ConditionalMargin(margin, filter=Condition(lambda: not self.is_welcome()))
            for margin in self.body.window.right_margins]
        self.prompt = TextArea(height=lambda: display.view.theme.spacing.prompt_rows,
                               multiline=True,wrap_lines=True,
                               input_processors=[TerminalInputProcessor(lambda:display.view.caps.unicode)],
                               completer=SlashCommandCompleter(),
                               complete_while_typing=True, style='class:prompt')
        self.sidebar = Window(FormattedTextControl(self.sidebar_text),
                              width=lambda: display.view.theme.spacing.sidebar_width,
                              wrap_lines=True)
        bindings = KeyBindings()
        @bindings.add('enter',filter=has_focus(self.prompt))
        def submit(event):
            raw = self.prompt.text.strip()
            was_busy = self.busy
            if raw and (not was_busy or presentation_command(raw)):
                self.prompt.text = ''
                task = event.app.create_background_task(self.submit(raw))
                if not was_busy and not presentation_command(raw):
                    self.task = task
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
            elif not self.cancel_pairing():
                self.prompt.text = ''
        @bindings.add('tab')
        def focus(event):
            if has_focus(self.prompt)() and self.prompt.text.lstrip().startswith('/'):
                buf = self.prompt.buffer
                if buf.complete_state:
                    buf.complete_next()
                else:
                    buf.start_completion(select_first=True)
            else:
                event.app.layout.focus_next()
        @bindings.add('s-tab')
        def previous(event):
            if has_focus(self.prompt)() and self.prompt.buffer.complete_state:
                self.prompt.buffer.complete_previous()
            else:
                event.app.layout.focus_previous()
        @bindings.add('f2')
        def theme_picker(event):
            event.app.create_background_task(self.choose_theme())
        @bindings.add('f3')
        def model_picker(event):
            event.app.create_background_task(self.choose_model())
        @bindings.add('f4')
        def page(event):
            self.home_open = False
            pages = self.PAGES
            cur = pages.index(self.page) if self.page in pages else 0
            self.page = pages[(cur + 1) % len(pages)]
            self.refresh_body()
        for idx, p_name in enumerate(self.PAGES, start=1):
            def _make_jump(target_page):
                def _jump(event):
                    self.home_open = False
                    self.page = target_page
                    self.refresh_body()
                return _jump
            bindings.add('escape', str(idx))(_make_jump(p_name))
        @bindings.add('f5')
        def refresh(event):
            self._backoff = 0
            event.app.create_background_task(self.discover())
        @bindings.add('c-n')
        def fresh(event):
            if not self.busy:
                self.new_conversation()

        theme_open = Condition(lambda:self._theme_selection is not None)
        @bindings.add('up',filter=theme_open)
        @bindings.add('left',filter=theme_open)
        def previous_theme(event):
            self.preview_theme(-1)
        @bindings.add('down',filter=theme_open)
        @bindings.add('right',filter=theme_open)
        @bindings.add('tab',filter=theme_open)
        def next_theme(event):
            self.preview_theme(1)
        @bindings.add('enter',filter=theme_open)
        def commit_theme(event):
            self.finish_theme_preview(commit=True)
        @bindings.add('escape',filter=theme_open,eager=True)
        def cancel_theme(event):
            self.finish_theme_preview(commit=False)

        header = Window(FormattedTextControl(self.header),height=3,style='class:header')
        companion = lambda stacked: Window(FormattedTextControl(lambda:self.companion_text(stacked)),
                                            height=self.companion_height)
        sidebar = ConditionalContainer(
            HSplit([companion(True), HorizonFrame(self.sidebar,title='Mission · critères & preuves')]),
            Condition(lambda:self.wide_workspace() and not self.is_welcome()))
        ribbon = ConditionalContainer(companion(False),
            Condition(lambda:not self.wide_workspace() and not self.is_welcome()))
        slash_dock = ConditionalContainer(
            Window(FormattedTextControl(self.slash_dock_text), height=2, style='class:dock'),
            Condition(lambda:self.prompt.text.lstrip().startswith('/')))
        main_body = VSplit([self.body, sidebar], padding=lambda:display.view.theme.spacing.gutter)
        root = HSplit([
            header,
            Window(FormattedTextControl(self.tabs),height=1),
            Window(height=lambda:1 if self.is_welcome() else 0),
            ribbon,
            main_body,
            Window(FormattedTextControl(self.activity_text), height=self.activity_height),
            slash_dock,
            HorizonFrame(self.prompt,title=self.prompt_title),
            Window(FormattedTextControl(self.footer),height=2,style='class:footer'),
        ])
        theme_panel = ConditionalContainer(
            HorizonFrame(Window(FormattedTextControl(self.theme_picker_text),style='class:dialog.body',wrap_lines=True),
                         title='Thèmes · aperçu immédiat'),theme_open)
        layout = FloatContainer(content=root, floats=[
            Float(xcursor=True,ycursor=True,content=CompletionsMenu(max_height=8,scroll_offset=1)),
            Float(content=theme_panel,width=lambda:min(68,self.app.output.get_size().columns-4),height=15)])
        self.app = Application(layout=Layout(layout,focused_element=self.prompt),key_bindings=bindings,
                               full_screen=True,mouse_support=True,style=self.style(),
                               color_depth=color_depth(display.view.caps),
                               terminal_size_polling_interval=None if os.name=='posix' else 1,
                               input=input,output=output,min_redraw_interval=.05,max_render_postpone_time=.1,
                               before_render=self._before_render)
        self.refresh_body()

    def _before_render(self, app):
        width = self.body_width()
        if self._body_key and (self._body_key[1] != width or self._body_key[-1] != max(11,app.output.get_size().rows-14)):
            self.refresh_body(invalidate=False)

    def is_welcome(self):
        return self.page == "conversation" and not self.partial and (self.home_open or not self.transcript)

    def body_width(self):
        width = self.app.output.get_size().columns
        if self.wide_workspace() and not self.is_welcome():
            width -= display.view.theme.spacing.sidebar_width + display.view.theme.spacing.gutter + 4
        return max(20, width - 4)

    def wide_workspace(self):
        return self.app.output.get_size().columns >= 112

    def companion_height(self):
        rows = self.app.output.get_size().rows
        return min(14,max(10,(rows-13)//2)) if self.wide_workspace() else 4 if rows>=40 else 3

    def companion_text(self, stacked):
        from .welcome import render_companion
        width = display.view.theme.spacing.sidebar_width+2 if stacked else self.app.output.get_size().columns
        _,lines = render_companion(width,self.companion_height(),display.view.theme,display.view.caps,stacked=stacked)
        return [fragment for index,line in enumerate(lines)
                for fragment in ([('', '\n')] if index else [])+line]

    def welcome_text(self):
        return ('# Un horizon ouvert.\n\n'
                'Décrivez le résultat attendu.\n\n'
                '**/modules** Explorer  \n'
                '**/missions** Reprendre  \n'
                '**/theme** Changer d’univers  \n'
                '**/help** Commandes')

    def prompt_title(self):
        prefix = f'{self.active_module} · ' if self.active_module else ''
        return safe_text(prefix+'Demande · Entrée envoyer · Alt+Entrée ligne', unicode=display.view.caps.unicode)

    def tabs(self):
        width = self.app.output.get_size().columns
        theme = display.view.theme
        items = []
        for index, (page, label) in enumerate(PAGE_LABELS.items(), 1):
            short = label if width >= 90 else label[:3] if width < 70 else label[:4]
            if page == self.page:
                short = theme.components.tab_open+short+theme.components.tab_close
            items.append(('class:tab.active' if page == self.page else 'class:footer', f' {index} {short} '))
        return [(style, safe_text(text, unicode=display.view.caps.unicode)) for style,text in items]

    def slash_dock_text(self):
        raw = self.prompt.text.lstrip()
        if not raw.startswith('/'):
            return []
        token = raw.split()[0].lower()
        matches = [item for item in SLASH_COMMANDS_CATALOG if item[0].startswith(token)]
        if not matches:
            return [('class:warning', ' Commande inconnue · /help pour la liste')]
        width = self.app.output.get_size().columns-2
        line = '  '.join(command for command, *_ in matches[:5])
        return [('class:tab.active', fit(line,width,display.view.caps.unicode)+'\n'),
                ('class:footer',fit(matches[0][3]+' · Tab compléter',width,display.view.caps.unicode))]

    def page_title(self):
        return PAGE_LABELS.get(self.page, self.page)

    def modules_cockpit_text(self):
        lines = ['# Modules', '', 'Choisir un parcours oriente la demande ; la disponibilité des moteurs se vérifie séparément.', '']
        commands = {'CHAT':'chat','CODE':'code','IMAGE':'image','3D':'3d','CYBER':'cyber',
                    'BUSINESS':'business','LEARNING':'academy','VOICE':'voice'}
        for key, meta in STUDIO_MODULES_META.items():
            selected = ' · sélectionné' if key == self.active_module else ''
            lines.extend([f"### /{commands[key]} · {meta['title']}{selected}", meta['capabilities'], ''])
        lines.append('**F5 /doctor** actualise les moteurs observés.')
        return '\n'.join(lines)

    def agi_cognitive_report(self, query):
        request = query or self.state.goal
        return ('### Analyse · observations disponibles\n\n'
                + ('Objectif : '+plain(request)+'\n\n' if request else 'Aucun objectif actif.\n\n')
                + f'Plan reçu : {len(self.state.plan)} étapes. Critères vérifiés : {len(self.state.verified)}.\n\n'
                + 'Cette vue décrit les événements reçus. Elle ne mesure pas une capacité AGI ni un raisonnement interne.')

    def agents_matrix_text(self):
        lines = ['# Agents & outils', '', f'État reçu : **{self.state.status}**', '',
                 'Seules les actions réellement reçues sont listées.', '']
        for event in self.state.evidence[-30:]:
            label = event.get('worker_id') or event.get('agent') or event.get('tool') or 'Outil'
            lines.extend([f"### {plain(label)} · {'réussi' if event.get('ok') else 'échec'}",
                          observation_text(event), ''])
        if not self.state.evidence:
            lines += ['Aucune action observée pour cette session.', '',
                      'Le registre distant est consultable avec `jobia agents list`.']
        return '\n'.join(lines)

    # --- Skills and MCP -------------------------------------------------------

    @property
    def active_skills(self):
        names = getattr(self.history, 'state', {}).get('active_skills', []) if hasattr(self.history, 'state') else \
            getattr(self, '_active_skills', [])
        return [n for n in names if isinstance(n, str)]

    def _set_active_skills(self, names):
        if hasattr(self.history, 'state'):
            self.history.state['active_skills'] = list(names)
            if hasattr(self.history, 'save'):
                self.history.save()
        else:
            self._active_skills = list(names)

    def request_text(self, raw):
        """The request sent to an engine: activated skills travel with it."""
        names = self.active_skills
        if not names:
            return raw
        from . import skills as library
        block = library.context_block(names)
        return (block + '\n\n[DEMANDE]\n' + raw) if block else raw

    def load_integrations(self, *, remote=True):
        """Local inventories (instant) and the bridge's (when paired)."""
        from . import mcp_registry, skills as library
        rows = library.discover(include_disabled=True)
        self.skill_rows = [s.to_dict() for s in rows]
        cache = mcp_registry.load_cache()
        self.mcp_rows = [{'name': name, 'describe': mcp_registry.describe(cfg), 'scope': cfg.get('scope', ''),
                          'disabled': bool(cfg.get('disabled')),
                          'tools': [t.get('name', '') for t in cache.get(name, {}).get('tools', [])],
                          'error': cache.get(name, {}).get('error', '')}
                         for name, cfg in mcp_registry.servers(include_disabled=True).items()]
        if remote and config.is_configured() and config.get('mode', 'auto') != 'local':
            try:
                with self.bridge_factory(timeout=15) as client:
                    listed = client.skills_list() if hasattr(client, 'skills_list') else {}
                    servers = client.mcp_list() if hasattr(client, 'mcp_list') else {}
                if listed.get('ok'):
                    self.remote_skill_rows = [r for r in listed.get('skills', []) if isinstance(r, dict)]
                if servers.get('ok'):
                    self.remote_mcp_rows = [r for r in servers.get('servers', []) if isinstance(r, dict)]
            except Exception:
                pass
        self.integrations_loaded = time.monotonic()

    async def refresh_integrations(self, *, remote=True):
        await asyncio.to_thread(self.load_integrations, remote=remote)
        self.refresh_body()

    def skills_view_text(self):
        active = set(self.active_skills)
        lines = ['# Skills', '',
                 'Instructions réutilisables. **/skill NOM** les active pour vos prochaines demandes '
                 '(local et PC Aurora) ; **Ctrl+K** cherche partout.', '']
        if active:
            lines += ['### Actifs pour cette conversation', '', *(f'- ● **{plain(n)}**' for n in sorted(active)), '']
        lines += ['### Sur cet ordinateur', '']
        if self.skill_rows:
            for row in self.skill_rows:
                mark = '●' if row['name'] in active else '○' if row.get('enabled', True) else '×'
                state = '' if row.get('enabled', True) else ' · désactivé'
                lines.append(f"- {mark} **{plain(row['name'])}** · {plain(row.get('level', ''))}{state}  \n"
                             f"  {plain(row.get('description', ''))[:180]}")
        else:
            lines.append('Aucun skill local. **/skills import anthropics/skills** ajoute un catalogue public.')
        if self.remote_skill_rows:
            lines += ['', '### Sur le PC Aurora', '']
            lines += [f"- **{plain(r.get('name', '?'))}** · {plain(r.get('description', ''))[:160]}"
                      for r in self.remote_skill_rows[:60]]
        lines += ['', '**/skills chercher TEXTE** · **/skills import SOURCE** (dossier, .zip, URL, owner/repo GitHub)']
        return '\n'.join(lines)

    def mcp_view_text(self):
        lines = ['# MCP', '', 'Serveurs d’outils. Ceux de cet ordinateur servent aux missions locales ; '
                 'ceux du PC Aurora aux missions distantes.', '', '### Sur cet ordinateur', '']
        if self.mcp_rows:
            for row in self.mcp_rows:
                state = 'désactivé' if row['disabled'] else (f"{len(row['tools'])} outils" if row['tools'] else
                                                              'catalogue à lire · /mcp refresh')
                if row.get('error'):
                    state = 'erreur · ' + plain(row['error'])[:80]
                lines.append(f"- **{plain(row['name'])}** · {state}  \n  {plain(row['describe'])[:160]}")
                if row['tools']:
                    lines.append('  ' + ' · '.join(plain(t) for t in row['tools'][:8])
                                 + (' …' if len(row['tools']) > 8 else ''))
        else:
            lines.append('Aucun serveur local. **/mcp import** reprend ceux de Claude, Cursor, VS Code, Windsurf, '
                         'Gemini ou Codex ; **/mcp chercher TEXTE** interroge aussi le registre officiel.')
        if self.remote_mcp_rows:
            lines += ['', '### Sur le PC Aurora', '']
            lines += [f"- **{plain(r.get('name', '?'))}** · {plain(r.get('description', '') or r.get('status', ''))[:160]}"
                      for r in self.remote_mcp_rows[:40]]
        lines += ['', '**/mcp import** · **/mcp refresh** · **/mcp install NOM** · '
                  '**/mcp appel SERVEUR OUTIL {json}**']
        return '\n'.join(lines)

    async def integrations_command(self, cmd, arg):
        """/skill, /skills and /mcp; long work runs off the interface thread."""
        from . import mcp_registry, skills as library
        words = arg.split(maxsplit=1)
        action = words[0].lower() if words else ''
        rest = words[1].strip() if len(words) > 1 else ''
        if cmd == '/skill':
            if not arg:
                self.note('Skills actifs : ' + (', '.join(self.active_skills) or 'aucun') +
                          '\n/skill NOM active ou retire un skill.')
                return
            skill = await asyncio.to_thread(library.get, arg)
            if skill is None:
                hits = await asyncio.to_thread(library.search, arg, limit=5)
                self.note('Skill introuvable : ' + plain(arg) + (
                    '\nProches : ' + ', '.join(s.name for _, s in hits) if hits else
                    '\n/skills chercher TEXTE --catalog pour en trouver.'))
                return
            names = self.active_skills
            if skill.name in names:
                names.remove(skill.name)
                self.show_toast(f'Skill retiré : {skill.name}', 'info')
            else:
                names.append(skill.name)
                self.show_toast(f'Skill actif : {skill.name} · joint à vos prochaines demandes', 'success')
            self._set_active_skills(names)
            self.refresh_body()
            return
        if cmd == '/skills':
            if action in {'chercher', 'search', 'cherche', 'find'} and rest:
                local = await asyncio.to_thread(library.search, rest, limit=8)
                lines = [f'### Skills · « {plain(rest)} »', '']
                lines += [f'- **{s.name}** · {s.level} · {plain(s.description)[:160]}' for _, s in local]
                if len(local) < 3:
                    self.show_toast('Recherche dans les catalogues publics…', 'info')
                    try:
                        hits, errors = await asyncio.to_thread(library.catalog_search, rest, limit=6)
                    except Exception as exc:
                        hits, errors = [], [str(exc)]
                    lines += [f'- **{s.name}** · catalogue {s.source} · /skills import {s.source} {s.name}  \n'
                              f'  {plain(s.description)[:160]}' for _, s in hits]
                    lines += [f'- catalogue indisponible : {plain(e)}' for e in errors]
                if len(lines) == 2:
                    lines.append('Aucun résultat.')
                self.note('\n'.join(lines))
                return
            if action == 'import' and rest:
                source, *names = rest.split()
                self.show_toast('Import de skills · ' + source, 'info', None)
                try:
                    report = await asyncio.to_thread(library.import_skills, source, names=names)
                except Exception as exc:
                    self.show_toast('Import impossible', 'warning')
                    self.note('Import impossible : ' + plain(exc))
                    return
                self.show_toast(f'{len(report.imported)} skill(s) importé(s)', 'success')
                self.note('### Skills importés\n\n' + (', '.join(report.imported) or 'aucun nouveau')
                          + (f"\n\nDéjà présents : {', '.join(report.skipped)}" if report.skipped else '')
                          + ('\n\nDisponibles dans la source : ' + ', '.join(report.available[:40])
                             if not report.imported and report.available else ''))
                await self.refresh_integrations(remote=False)
                return
            self.page, self.home_open = 'skills', False
            self.refresh_body()
            await self.refresh_integrations()
            return
        # /mcp
        if action in {'chercher', 'search', 'cherche', 'find'} and rest:
            local = await asyncio.to_thread(mcp_registry.search, rest, limit=10)
            lines = [f'### MCP · « {plain(rest)} »', '']
            lines += [f"- **{h['server']}:{h['name']}** · {h['kind']} · {plain(h['description'])[:140]}"
                      if h['kind'] != 'serveur' else f"- **{h['name']}** · serveur · {plain(h['description'])[:140]}"
                      for h in local]
            try:
                registry = await asyncio.to_thread(mcp_registry.registry_search, rest, limit=6)
            except Exception as exc:
                registry = []
                lines.append('Registre officiel indisponible : ' + plain(exc))
            if registry:
                lines += ['', '#### Registre officiel MCP', '']
                lines += [f"- **{r['name']}** {r['version']} · {plain(r['description'])[:140]}  \n"
                          f"  /mcp install {r['name']}" for r in registry]
            if len(lines) == 2:
                lines.append('Aucun résultat.')
            self.note('\n'.join(lines))
            return
        if action == 'import':
            def run_import():
                if rest:
                    found = mcp_registry.load_source(rest)
                else:
                    found = {}
                    for row in mcp_registry.scan_clients():
                        found.setdefault(row['name'], row['config'])
                return mcp_registry.import_servers(found, origin=rest or 'import')
            try:
                imported, skipped = await asyncio.to_thread(run_import)
            except Exception as exc:
                self.note('Import MCP impossible : ' + plain(exc))
                return
            self.show_toast(f'{len(imported)} serveur(s) MCP importé(s)', 'success' if imported else 'info')
            self.note('### MCP importés\n\n' + (', '.join(imported) or 'rien de nouveau')
                      + (f"\n\nConflits gardés : {', '.join(skipped)}" if skipped else ''))
            if imported:
                await asyncio.to_thread(mcp_registry.refresh, set(imported))
            await self.refresh_integrations(remote=False)
            return
        if action == 'refresh':
            self.show_toast('Lecture des catalogues MCP…', 'info', None)
            await asyncio.to_thread(mcp_registry.refresh)
            self.show_toast('Catalogues MCP à jour', 'success')
            await self.refresh_integrations(remote=False)
            return
        if action == 'install' and rest:
            name = rest.split()[0]
            try:
                rows = await asyncio.to_thread(mcp_registry.registry_search, name, limit=20)
                entry = next((r for r in rows if r['name'] == name), None) or (rows[0] if len(rows) == 1 else None)
                if entry is None:
                    raise ValueError('nom exact introuvable dans le registre officiel')
                alias = mcp_registry.default_alias(entry['name'])
                saved = mcp_registry.add(alias, mcp_registry.install_config(entry), overwrite=True)
            except Exception as exc:
                self.note('Installation MCP impossible : ' + plain(exc))
                return
            missing = mcp_registry.missing_values(saved)
            self.show_toast(f'MCP installé : {alias}', 'success')
            self.note(f'### MCP installé : {alias}\n\n{mcp_registry.describe(saved)}'
                      + ('\n\nÀ renseigner : ' + ', '.join(missing) if missing else ''))
            await self.refresh_integrations(remote=False)
            return
        if action in {'appel', 'call'} and rest:
            parts = rest.split(maxsplit=2)
            if len(parts) < 2:
                self.note('Usage : /mcp appel SERVEUR OUTIL {"argument": "valeur"}')
                return
            server, tool = parts[0], parts[1]
            try:
                arguments = json.loads(parts[2]) if len(parts) > 2 else {}
                if not isinstance(arguments, dict):
                    raise ValueError('objet JSON attendu')
            except ValueError as exc:
                self.note('Arguments JSON invalides : ' + plain(exc))
                return
            cfg = mcp_registry.servers().get(server)
            if cfg is not None:
                response, error = await asyncio.to_thread(
                    mcp_registry.call, cfg, 'tools/call', {'name': tool, 'arguments': arguments}, timeout=60)
                result = {'error': error} if error else response['result']
            else:
                def remote_call():
                    with self.bridge_factory(timeout=70) as client:
                        return client.mcp_call(server, tool, arguments=arguments)
                result = await asyncio.to_thread(remote_call)
            self.note(f'### MCP · {plain(server)}:{plain(tool)}\n\n```json\n'
                      + plain(json.dumps(result, ensure_ascii=False, indent=2))[:12000] + '\n```')
            return
        self.page, self.home_open = 'mcp', False
        self.refresh_body()
        await self.refresh_integrations()

    @property
    def busy(self):
        return self.task is not None and not self.task.done()

    def style(self):
        view = display.view
        if not view.caps.color:
            return Style.from_dict({'header':'bold', 'frame.label':'bold', 'frame.border':'',
                                    'success':'bold', 'warning':'bold', 'failure':'bold',
                                    'tab.active':'bold reverse', 'completion-menu.completion.current':'reverse',
                                    'button.focused':'reverse','radio-selected':'reverse','radio-checked':'bold'})
        palette = view.theme.palette
        base = dict(view.theme.prompt_toolkit_style(view.caps))
        base.update({'':'bg:'+palette.surface+' '+palette.text,
                     'header':'bold '+palette.primary,'footer':palette.muted,
                     'frame.border':palette.border,'frame.label':'bold '+palette.secondary,
                     'text-area':'bg:'+palette.surface+' '+palette.text,
                     'status':palette.accent,'prompt':palette.text,
                     'activity':palette.info,'success':palette.success,
                     'warning':palette.warning,'failure':'bold '+palette.error,
                     'tab.active':'bold '+palette.primary,'dock':'bg:'+palette.raised+' '+palette.text})
        return Style.from_dict(base)

    def target(self):
        if self._active_target:
            return self._active_target
        if config.get('mode','auto') == 'local':
            return 'cet ordinateur'
        return target_label(config.resolve_server_url())

    def session(self):
        return str(getattr(self.history, 'session_id', 'temporaire'))[:8]

    def header(self):
        width = self.app.output.get_size().columns-2
        caps, theme = display.view.caps, display.view.theme
        latency = f'{self.latency_ms:.0f} ms' if self.latency_ms is not None else 'n/d'
        mission = self.state.id or 'aucune'
        if width < 90:
            mission = mission[:16]
        identities = {'horizon':'A U R O R A', 'tidal':'≈ AURORA ≈', 'orbital':'◌ AURORA /',
                      'sunset':'▰ A U R O R A', 'terminal':'> AURORA_', 'facet':'◇ AURORA',
                      'block':'[ AURORA ]', 'editorial':'Aurora.'}
        heading = '  '+identities.get(theme.components.header,'AURORA')+'  /  '+theme.label
        first = fit(heading,width,caps.unicode)
        target = fit(self.target(), max(8,width-len(self.connection)-len(latency)-20),caps.unicode)
        second = fit(f'  Cible {target} · {self.connection} · Diag {latency}',width,caps.unicode)
        third = fit(f'  Session {self.session()} · Mission {mission}',width,caps.unicode)
        return [('class:header',first+'\n'),('class:status',second+'\n'),('class:footer',third)]

    @property
    def recovery_notices(self):
        if not self._recovery_notices_shown:
            return []
        saved = getattr(self.history,'state',{})
        notices = []
        if saved.get('active_mission_id'):
            notices.append('Mission conservée · /missions pour la retrouver')
        if saved.get('pending_request'):
            notices.append('Acceptation incertaine · /retry (même demande)'
                           if retry_payload_valid(saved['pending_request']) else
                           'Reprise incomplète · /retry pour récupérer le texte')
        return notices

    def activity_height(self):
        recovered = self.is_welcome() and len(self.recovery_notices)>1
        return 3 if self.app.output.get_size().rows>=28 or recovered else 2

    def activity_text(self):
        state = self.state
        active = state.status in {'starting','running','queued','permission'}
        quiet = max(0,int(time.monotonic()-(state.last_progress or state.started))) if state.started else 0
        signal_age = max(0,int(time.monotonic()-state.last_signal)) if state.last_signal else None
        if active and quiet>=15:
            label = f'Aucune avancée reçue depuis {quiet}s'
            label += ' · connexion vivante' if signal_age is not None and signal_age<15 and self.local_process is None else ' · en attente de nouvelles'
            style = 'warning'
        else:
            label = {'idle':'Prêt · aucun travail en cours','starting':'Envoi · attente d’acceptation',
                     'running':'Mission acceptée · '+plain(state.phase),'queued':'En file d’attente',
                     'permission':'Autorisation requise','completed':'Mission terminée','failed':'Mission en échec',
                     'interrupted':'Mission interrompue','stopped':'Mission arrêtée'}.get(state.status,state.status)
            style = 'failure' if state.status in {'failed','interrupted'} else 'activity'
        width = self.app.output.get_size().columns-2
        rows = [('class:'+style,fit(' Activité observée · '+label,width,display.view.caps.unicode)+'\n')]
        if self.is_welcome() and self.recovery_notices:
            if len(self.recovery_notices)==1 and self.activity_height()==3:
                rows.append(('class:footer',' Historique conservé · /view conversation\n'))
            rows.extend(('class:warning',fit(' '+text,width,display.view.caps.unicode)+'\n')
                        for text in self.recovery_notices)
            return [(tone,safe_text(text,unicode=display.view.caps.unicode)) for tone,text in rows]
        if self.app.output.get_size().rows>=28:
            counts = f' {len(state.evidence)} observations · {len(state.files)} fichiers · {state.received_chars} caractères reçus'
            rows.append(('class:footer',fit(counts,width,display.view.caps.unicode)+'\n'))
        if self.is_welcome() and self.history:
            rows.append(('class:footer',' Historique conservé · /view conversation pour le retrouver'))
        elif state.activity:
            tone,text = state.activity[-1]
            rows.append(('class:'+tone,fit(' '+text,width,display.view.caps.unicode)))
        else:
            rows.append(('class:footer',' Le plan et les outils apparaîtront ici.'))
        return [(style,safe_text(text,unicode=display.view.caps.unicode)) for style,text in rows]

    def footer(self):
        width = self.app.output.get_size().columns-2
        actions = ' F2 Thème  F3 Modèle  F4 Vue  F5 Diagnostic'
        status = ' Ctrl+C Arrêt · Ctrl+Q Sortie · Tab Défiler · / Aide'
        return [('class:footer',fit(actions,width,display.view.caps.unicode)+'\n'),
                ('class:footer',fit(status,width,display.view.caps.unicode))]

    def sidebar_text(self):
        state = self.state
        mod_focus = self.active_module or domain_label(state.goal)
        lines = [f'{mod_focus} · '+config.get('default_permissions','SAFE'),
                 f'Critères {len(state.verified)}/{len(state.criteria)} · Fichiers {len(state.files)}',
                 'PLAN']
        lines.extend(f'{i+1}. {plain(step)}' for i,step in enumerate(state.plan))
        if not state.plan:
            lines.append('En attente d’un plan enregistré')
        lines.extend(['','CRITÈRES'])
        ok, pending, failed = ('✓ ','○ ','× ') if display.view.caps.unicode else ('+ ','o ','x ')
        lines.extend((ok if c in state.verified else pending)+plain(c) for c in state.criteria)

        if state.criteria:
            done_cnt = len([c for c in state.criteria if c in state.verified])
            pct = int((done_cnt / len(state.criteria)) * 100)
            bar_len = 10
            filled = int(bar_len * (pct / 100))
            bar = "#" * filled + "-" * (bar_len - filled)
            lines.extend(["", f"PROGRESSION : [{bar}] {pct}%"])

        lines.extend(['','OBJECTIF',plain(state.goal),'','OBSERVATIONS'])
        lines.extend((ok if e.get('ok') else failed)+e.get('tool','')+' · '+e.get('id','')+
                     '\n  '+observation_text(e, 250) for e in state.evidence[-8:])
        if state.metrics.get('tokens_per_second') is not None:
            lines.extend(['',f"Débit observé : {state.metrics['tokens_per_second']:.1f} tokens/s"])
        lines.append(f'Fichiers disponibles : {len(state.files)}')
        return safe_text('\n'.join(lines), unicode=display.view.caps.unicode)

    def avant_apres_text(self):
        lines = ['# Bilans & mesures', '',
                 'Aucun rapport comparatif vérifié n’a été reçu pour cette session.', '',
                 'Les anciens scores ne constituent pas une mesure de la mission actuelle.', '']
        if self.state.metrics:
            lines.extend(['### Mesures reçues', '', '```json', json.dumps(self.state.metrics,ensure_ascii=False,indent=2), '```'])
        if self.state.files:
            lines.extend(['### Fichiers reçus', '', *('- '+plain(path) for path in self.state.files)])
        return '\n'.join(lines)

    def ontologie_report(self, query):
        request = query or self.state.goal
        return ('### Contraintes de la demande\n\n'+(plain(request) if request else 'Aucune demande à examiner.')+
                '\n\nLe CLI transmet la demande au moteur. Style, traduction et animation ne sont pas confirmés '
                'sans événements ou fichiers vérifiés. Soumettez la demande pour observer son exécution.')

    def body_source(self):
        if self.page == 'conversation':
            text = self.welcome_text() if self.is_welcome() else self.transcript
        elif self.page == 'modules':
            text = self.modules_cockpit_text()
        elif self.page == 'agents':
            text = self.agents_matrix_text()
        elif self.page == 'skills':
            text = self.skills_view_text()
        elif self.page == 'mcp':
            text = self.mcp_view_text()
        elif self.page == 'avant_apres':
            text = self.avant_apres_text()
        elif self.page == 'journal':
            text = self.journal or 'Aucun événement reçu.'
        else:
            text = '# Fichiers reçus\n\n'+('\n'.join('- '+plain(p) for p in self.state.files) or 'Aucun fichier reçu. Les livraisons vérifiées apparaîtront ici.')
        if self.page == 'conversation' and self.partial:
            tail = ' · derniers 16 000 caractères' if len(self.partial)==16000 else ''
            text += '\n\n### SORTIE REÇUE · résultat non encore confirmé'+tail+'\n\n'+self.partial
        if len(text)>64000:
            text = ('Aperçu limité aux derniers 64 000 caractères ; conversation complète conservée dans l’archive.\n\n'+text[-64000:])
        return text

    def refresh_body(self, *, invalidate=True):
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None
        source = self.body_source()
        width = self.body_width()
        height = max(11,self.app.output.get_size().rows-14)
        key = (source,width,display.view.theme.id,display.view.caps.color_depth,display.view.caps.unicode,self.page,height)
        if key != self._body_key:
            changed_page = self._body_page != self.page
            previous = self.body.buffer.cursor_position
            follow = self.app.layout.has_focus(self.prompt)
            if self.is_welcome():
                from .welcome import render_welcome
                text,lines = render_welcome(width,height,display.view.theme,display.view.caps)
            else:
                text,lines = render_document(source,width,display.view.theme,display.view.caps,journal=self.page=='journal')
            self.body_lexer.lines = lines
            position = 0 if changed_page or self.is_welcome() else len(text) if follow else min(previous,len(text))
            self.body.buffer.set_document(Document(text,position),bypass_readonly=True)
            self._body_key,self._body_source,self._body_page = key,source,self.page
        if invalidate:
            self.app.invalidate()

    def schedule_refresh(self):
        # Coalesce token bursts and journal updates into at most ten body renders/s.
        if self._refresh_handle is None:
            try:
                self._refresh_handle = asyncio.get_running_loop().call_later(.1, self.refresh_body)
            except RuntimeError:
                self.refresh_body()

    def note(self,text,*,reveal=True):
        if reveal:
            self.home_open = False
        self.transcript = (self.transcript+'\n'+plain(text)+'\n')[-200000:]
        self.refresh_body()

    def post(self,callback,*args):
        self.loop.call_soon_threadsafe(callback,*args)

    def event(self,event):
        self.state.consume(event)
        kind = event.get('type')
        if kind == 'reconnecting':
            self.connection = 'reconnexion'
        elif kind not in {'error','mission_interrupted'} and self._active_target != 'cet ordinateur':
            self.connection = 'flux reçu'
        self.ensure_clock()
        if kind not in {'token','heartbeat'}:
            self.journal = (self.journal+'\n'+plain(json.dumps(event,ensure_ascii=False,indent=2)))[-200000:]
        if kind == 'token':
            if event.get('channel')=='tool_call':
                self.journal = (self.journal+plain(event.get('content','')))[-200000:]
            else:
                self.partial = (self.partial + plain(event.get('content','')))[-16000:]
        if kind=='mission_complete':
            self.partial = ''
            if hasattr(self.history,'state'):
                self.history.state.pop('active_mission_id',None)
            result = event.get('result','')
            self.note('\n### AURORA\n\n'+result)
            if result:
                if not self.history or self.history[-1] != {'role':'assistant','content':result}:
                    self.history.append({'role':'assistant','content':result})
                if hasattr(self.history,'save'):
                    self.history.save()
        elif kind in {'error','mission_interrupted'}:
            self.note(event.get('message',event.get('error','Mission interrompue.'))+'\nÉtat conservé. /missions puis /resume ID pour reprendre.')
        elif kind in {'permission_request','permission_required'}:
            self.note('### Autorisation requise\n\n'+plain(event.get('message','Confirmation nécessaire sur le PC maître'))+
                      '\n\nCe pont ne propose pas de confirmation dans le CLI. Inspectez la demande sur le PC maître ; Ctrl+C demande l’arrêt.')
        elif kind=='file_received':
            self.note('Fichier reçu et vérifié : '+event['path'])
        elif kind=='plan' and not event.get('worker'):
            self.note('### PLAN REÇU\n\n'+'\n'.join(f'{i+1}. {plain(step)}' for i,step in enumerate(self.state.plan)))
        elif kind=='plan_revision' and not event.get('worker'):
            self.note('ÉTAPES ADAPTÉES · '+plain(event.get('reason','Observations nouvelles'))+'\n'+
                      '\n'.join(f'{i+1}. {plain(step)}' for i,step in enumerate(self.state.plan)))
        elif kind in {'environment_observation','tool_result','recovery_observation','completion_observation','stagnation_notice',
                      'recovery_start','recovery_proposal','recovery_rejected','review_result'}:
            detail = observation_text(event)
            if detail:
                label = ('OUTIL · '+str(event.get('tool', ''))+' · '+('réussi' if event.get('ok') else 'échec')) if kind=='tool_result' else 'OBSERVATION'
                self.note(label+'\n'+detail)
        self.schedule_refresh()

    def connection_state(self) -> str:
        """Normalised connection state shared by both renderers."""
        return {'connecté': 'online', 'flux reçu': 'online', 'dégradé': 'degraded',
                'reconnexion': 'reconnecting', 'hors ligne': 'offline', 'local': 'local',
                'autorisation requise': 'unpaired', 'non configuré': 'unpaired',
                'appairage': 'pairing'}.get(self.connection, 'checking')

    def reconnect_in(self):
        if self.reconnect_at is None or self.connection_state() not in {'offline', 'reconnecting'}:
            return None
        return max(0, int(round(self.reconnect_at - time.monotonic())))

    def show_toast(self, text, tone='info', seconds=6.0):
        """A transient notice; ``seconds=None`` keeps it until replaced."""
        self.toast = {'text': plain(text), 'tone': tone,
                      'until': None if seconds is None else time.monotonic() + seconds}
        try:
            loop = asyncio.get_running_loop()
            if seconds is not None:
                loop.call_later(seconds + .05, self.app.invalidate)
        except RuntimeError:
            pass
        self.app.invalidate()

    def active_toast(self):
        toast = self.toast
        if toast is None:
            return None
        if toast['until'] is not None and time.monotonic() >= toast['until']:
            self.toast = None
            return None
        return toast

    async def discover(self, *, quiet=False):
        previous_url = config.resolve_server_url()
        try:
            self.connection = 'diagnostic'
            self.app.invalidate()
            mode = config.get('mode', 'auto')
            if not config.is_configured() and config.get('api_key') and mode != 'local':
                # A remembered authorisation without address: find the PC.
                from .connect import discover_tunnel
                try:
                    config.set_key('server_url', await asyncio.to_thread(discover_tunnel))
                except Exception:
                    pass
            if config.is_configured():
                def remote():
                    # Doctor checks several services; its response can take
                    # longer than the former five-second read budget.
                    with self.bridge_factory(timeout=30) as client:
                        initial_url = getattr(client, 'server_url', '')
                        started = time.monotonic()
                        doctor = client.doctor()
                        latency = (time.monotonic()-started)*1000
                        t_models = time.monotonic()
                        models = client.models() if doctor.get('ok') else {}
                        if getattr(client, 'server_url', '') != initial_url:
                            latency = (time.monotonic()-t_models)*1000
                    return doctor,models,latency
                doctor,models,self.latency_ms = await asyncio.to_thread(remote)
                from .bridge import failure_kind
                ready = doctor.get('ok') and doctor.get('ready', True)
                if doctor.get('http_status') in (401, 403):
                    self.connection = 'autorisation requise'
                    self.health = ('Autorisation refusée par le PC · /connect affiche un code à valider sur le PC · '
                                   + target_label(config.resolve_server_url()))
                    self.latency_ms = None
                    if not self._pairing_attempted and self.pairing is None and mode != 'local':
                        self._pairing_attempted = True
                        asyncio.get_running_loop().create_task(self.start_pairing())
                    self.app.invalidate()
                    return
                if not doctor.get('ok') and failure_kind(doctor):
                    raise ConnectionError(doctor.get('error') or 'PC Aurora injoignable')
                self.connection = 'connecté' if ready else 'dégradé'
                self._backoff = 0
                self.reconnect_at = None
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
                self.health += ' · '+target_label(config.resolve_server_url())
                if not ready:
                    reason = doctor.get('error') or '; '.join(
                        str(c.get('name', 'Contrôle'))+': '+str(c.get('detail') or 'échec')
                        for c in doctor.get('checks', []) if c.get('ok') is False
                    )
                    self.health += ' · '+plain(reason or 'jobia doctor --remote')
                if mode == 'local':
                    self.health += ' · Mode local actif : les demandes sont exécutées sur cet ordinateur'
                current_url = config.resolve_server_url()
                if previous_url and current_url != previous_url:
                    self.show_toast('Aurora a changé d’adresse · reconnecté à '+target_label(current_url), 'success')
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
                self.health = f'{len(self.models)} modèle(s) servi(s) localement · /connect associe le PC Aurora'
                self.connection = 'local'
                self._active_target = 'cet ordinateur'
                if mode != 'local' and not self.publication_hint:
                    asyncio.get_running_loop().create_task(self._publication_hint())
        except Exception as exc:
            self.connection = 'hors ligne'
            self.latency_ms = None
            self.health = 'PC Aurora injoignable · '+plain(exc)+' · nouvel essai automatique'
        if self.connection in {'dégradé','hors ligne'} and not quiet:
            if self.health != self._last_connection_warning:
                self.note('### Connexion à rétablir\n\n'+self.health+
                          '\n\nReconnexion automatique dès que le PC répond (nouvelle adresse comprise) · F5 réessayer maintenant.')
                self._last_connection_warning = self.health
        elif self.connection not in {'dégradé','hors ligne'}:
            self._last_connection_warning = ''
        self.ensure_clock()
        self.app.invalidate()

    async def _publication_hint(self):
        """Without pairing, tell the user when the PC is online and ready to pair."""
        from .connect import discover_tunnel
        try:
            await asyncio.to_thread(discover_tunnel)
        except Exception:
            return
        if not config.is_configured() and self.pairing is None:
            self.publication_hint = 'PC Aurora en ligne · /connect pour associer cet ordinateur'
            self.show_toast(self.publication_hint, 'info', 12)

    def _alive(self):
        with self.bridge_factory(timeout=8) as client:
            alive = getattr(client, 'alive', None)
            return alive() if callable(alive) else {'ok': True}

    def _monitor_delay(self):
        state = self.connection_state()
        if state in {'offline', 'reconnecting'}:
            return (2, 4, 8, 15, 30, 30)[min(self._backoff, 5)]
        return 20.0

    async def monitor_once(self):
        """One observation of the connection; returns the delay before the next."""
        mode = config.get('mode', 'auto')
        if self.pairing is not None or mode == 'local' or not config.is_configured():
            return 30.0
        if self.busy and self.local_process is None:
            return 15.0  # A remote mission stream reconnects by itself.
        state = self.connection_state()
        if state in {'online', 'degraded'}:
            alive = await asyncio.to_thread(self._alive)
            if alive.get('ok'):
                return 20.0
            self.connection = 'reconnexion'
            self.app.invalidate()
        await self.discover(quiet=state in {'offline', 'reconnecting'})
        if self.connection_state() in {'online', 'degraded'}:
            if state in {'offline', 'reconnecting'}:
                self.show_toast('Aurora est de nouveau joignable', 'success')
            return 20.0
        self._backoff = min(self._backoff + 1, 5)
        return self._monitor_delay()

    async def monitor(self):
        """Watch the connection while the interface is open (idle-friendly)."""
        self.loop = asyncio.get_running_loop()
        while True:
            delay = self._monitor_delay()
            if self.connection_state() in {'offline', 'reconnecting'}:
                self.reconnect_at = time.monotonic() + delay
                self.ensure_clock()
            await asyncio.sleep(delay)
            self.reconnect_at = None
            try:
                await self.monitor_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.health = 'Surveillance de connexion : '+plain(exc)
                self._backoff = min(self._backoff + 1, 5)

    async def start_pairing(self, url=''):
        """Show a pairing code here; the PC approves it in its own window."""
        if self.pairing is not None:
            self.note('Appairage déjà en cours · code '+(self.pairing.get('code') or 'en attente'))
            return
        import threading
        from . import tunnel
        from .connect import discover_tunnel, _register
        self.loop = asyncio.get_running_loop()
        target = (url or config.resolve_server_url() or '').rstrip('/')
        if not url and (not target or tunnel.is_ephemeral(target)):
            try:
                target = await asyncio.to_thread(discover_tunnel)
            except Exception as exc:
                if not target:
                    self.note('### PC Aurora introuvable\n\n'+plain(exc)+
                              '\n\nAllumez Aurora sur le PC : la connexion se fera dès que son adresse sera publiée.'
                              ' Adresse manuelle : /connect https://ADRESSE')
                    return
        try:
            tunnel.valid_url(target)
        except ValueError as exc:
            self.note('Adresse invalide : '+plain(exc))
            return
        cancel = threading.Event()
        self._pairing_cancel = cancel
        self.pairing = {'code': '', 'target': target_label(target), 'expires_at': None,
                        'status': 'Demande envoyée au PC…'}
        self.connection = 'appairage'
        self.show_toast('Appairage · demande envoyée à '+target_label(target), 'info', None)

        def on_code(code, seconds):
            self.post(self._pairing_code, code, seconds)

        def on_status(kind, message):
            self.post(self._pairing_status, kind, message)

        def work():
            from .device_pairing import pair_device
            credential = pair_device(target, on_code=on_code, on_status=on_status, cancel=cancel)
            response = _register(target, credential)
            response.raise_for_status()
            if not response.json().get('ok'):
                raise ValueError('Le PC a refusé l’enregistrement de cet appareil.')
            config.set_key('server_url', target)
            config.set_key('api_key', credential)

        try:
            await asyncio.to_thread(work)
        except Exception as exc:
            self.pairing = None
            self._pairing_cancel = None
            self.connection = 'non configuré' if not config.is_configured() else 'autorisation requise'
            self.show_toast('Appairage interrompu · /connect pour réessayer', 'warning', 10)
            self.note('Appairage interrompu : '+plain(exc)+'\n\n/connect relance la demande.')
            return
        self.pairing = None
        self._pairing_cancel = None
        self.publication_hint = ''
        self._active_target = ''
        self.show_toast('Appareil autorisé · connexion établie', 'success')
        self.note('### Connecté\n\nCet ordinateur est autorisé par le PC Aurora. '
                  'Il retrouvera seul la nouvelle adresse après chaque redémarrage.')
        await self.discover()

    def _pairing_code(self, code, seconds):
        if self.pairing is None:
            return
        self.pairing.update(code=code, expires_at=time.monotonic()+seconds,
                            status='Saisissez ce code sur le PC Aurora')
        spaced = code[:3]+' '+code[3:]
        self.show_toast(f'Code de connexion {spaced} · à saisir sur le PC Aurora', 'accent', None)
        self.note(f'### Code de connexion : {spaced}\n\n'
                  'Sur le PC Aurora, saisissez ce code dans la fenêtre de connexion. '
                  'Aucune clé à copier ; Ctrl+C annule la demande.')
        self.ensure_clock()

    def _pairing_status(self, kind, message):
        if self.pairing is not None:
            self.pairing['status'] = plain(message)
        self.app.invalidate()

    def cancel_pairing(self):
        if self._pairing_cancel is not None:
            self._pairing_cancel.set()
            return True
        return False

    async def choose_theme(self):
        if self._theme_selection is not None:
            return
        self._theme_original = display.view.theme.id
        self._theme_selection = themes.theme_ids().index(self._theme_original)
        self._theme_future = asyncio.get_running_loop().create_future()
        self.app.invalidate()
        try:
            await self._theme_future
        finally:
            if self._theme_selection is not None:
                self.finish_theme_preview(commit=False)

    def preview_theme(self, offset):
        if self._theme_selection is None:
            return
        self._theme_selection = (self._theme_selection+offset)%len(themes.all_themes())
        display.view.use_theme(themes.all_themes()[self._theme_selection].id)
        self.app.style = self.style()
        self.refresh_body()

    def finish_theme_preview(self, *, commit):
        if self._theme_selection is None:
            return
        if commit:
            config.set('theme',display.view.theme.id)
        else:
            display.view.use_theme(self._theme_original)
        self._theme_selection = None
        self.app.style = self.style()
        self.refresh_body()
        if self._theme_future and not self._theme_future.done():
            self._theme_future.set_result(None)

    def theme_picker_text(self):
        if self._theme_selection is None:
            return []
        rows = [('class:footer',' Flèches explorer · Entrée garder · Échap annuler\n\n')]
        width = min(64,self.app.output.get_size().columns-8)
        for index,theme in enumerate(themes.all_themes()):
            selected = index==self._theme_selection
            rows.append(('class:tab.active' if selected else 'class:text-area',
                         fit((' > ' if selected else '   ')+theme.label,width,display.view.caps.unicode)+'\n'))
        current = themes.all_themes()[self._theme_selection]
        rows += [('class:footer','\n '+fit(current.description,width,display.view.caps.unicode)),
                 ('class:success','\n\n Aperçu actif ; la configuration reste inchangée avant validation.')]
        return [(style,safe_text(text,unicode=display.view.caps.unicode)) for style,text in rows]

    async def choose_model(self):
        if self.busy:
            self.note('Le modèle de la mission active reste inchangé.')
            return
        name = await radiolist_dialog(title='Modèle IA & Moteur',text='Modèles observés ; leur qualité doit être évaluée sur ta tâche.',
                                     values=[('', 'Réglage par défaut du moteur')]+[(n,n) for n in dict.fromkeys(self.models)],style=self.style()).run_async()
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
        self.active_module = ''
        self.home_open = True
        self.state = MissionView()
        self.refresh_body()

    def _remote(self,raw,*,resume=None,attach=None,pending=None):
        from .bridge import Bridge
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
                payload = pending or {'request':self.request_text(raw),'workspace':str(Path.cwd()) if local_server else '',
                    'permissions':config.get('default_permissions','SAFE'),'model':config.get('default_model',''),
                    'idempotency_key':uuid4().hex}
                if 'references' not in payload and isinstance(client, Bridge):
                    payload['references'] = client.prepare_references(raw)
                    if hasattr(self.history,'state'):
                        self.history.state['pending_request'] = payload
                        self.history.save()
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
                    self.draft_restore = {'revision':(self.draft_restore or {}).get('revision',0)+1,
                                          'text':request}
                self.note('Reprise enregistrée incomplète : impossible de garantir une relance sans doublon. '
                          'Archive conservée, aucune mission lancée. Vérifie /missions et utilise /attach ID si elle existe. '
                          + ('La demande est récupérée ; Entrée créera une nouvelle mission.\n\n'+request
                             if isinstance(request, str) and request.strip() else 'Retape ta demande si elle doit être créée à nouveau.'))
                return
            raw = pending['request']
        if raw.startswith('/'):
            parts = raw.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ''
            if cmd=='/quit':
                self.app.exit()
                return
            if cmd=='/new':
                self.new_conversation()
                return
            if cmd=='/clear':
                from .core.conversation import Conversation, clear_archives
                try:
                    count, backup = clear_archives()
                    self.history = Conversation.open()
                except (OSError, ValueError, RuntimeError) as exc:
                    self.note('Nettoyage impossible : '+plain(exc))
                    return
                self.transcript, self.journal = '', ''
                self.partial = ''
                self.active_module = ''
                self.state, self.page = MissionView(), 'conversation'
                self.prompt.text = ''
                self.note(f'{count} conversation(s) locale(s) effacée(s) pour ce dossier. Nouvelle conversation.'
                          + (f'\nSauvegarde : {backup}' if backup else ''))
                return
            if cmd=='/theme':
                if arg:
                    if not themes.is_known(arg):
                        self.note('Thème inconnu : '+plain(arg))
                        return
                    display.view.use_theme(arg)
                    config.set('theme',display.view.theme.id)
                    self.app.style = self.style()
                    self.refresh_body()
                else:
                    await self.choose_theme()
                return
            if cmd=='/home':
                self.page, self.home_open = 'conversation', True
                self.refresh_body()
                return
            if cmd=='/models':
                await self.choose_model()
                return
            if cmd in {'/mode','/permissions'}:
                field = 'mode' if cmd=='/mode' else 'default_permissions'
                choices = ['auto','local','remote'] if cmd=='/mode' else ['SAFE','STANDARD','AUTONOMOUS','FULL']
                selected = await radiolist_dialog(title=cmd[1:],text='Choisis le fonctionnement explicite des prochaines missions.',
                                                 values=[(v,v) for v in choices],style=self.style()).run_async()
                if selected:
                    config.set(field,selected)
                return
            if cmd=='/doctor':
                self._backoff = 0
                await self.discover()
                self.note('Diagnostic mis à jour : ' + self.health)
                return
            if cmd in {'/connect', '/pair'}:
                await self.start_pairing(arg)
                return
            if cmd in {'/skill', '/skills', '/mcp'}:
                await self.integrations_command(cmd, arg)
                return
            if cmd in {'/avant-apres', '/gains'}:
                self.page = 'avant_apres'
                self.note(self.avant_apres_text())
                return
            if cmd in {'/fin-cycle', '/stop-cycle'}:
                self.note('### Contrôle indisponible\n\n'
                          'Le pont publié ne fournit pas de commande d’arrêt de cycle distant. '
                          'Aucun entraînement n’a été modifié. Utilisez le contrôle Auto-RL sur le PC maître.')
                return
            if cmd == '/ontologie':
                self.page = 'conversation'
                self.note(self.ontologie_report(arg))
                return
            if cmd=='/modules':
                self.page = 'modules'
                self.refresh_body()
                return
            if cmd=='/view':
                self.home_open = False
                target = arg.lower()
                alias_map = {
                    'chat': 'conversation', 'dialogue': 'conversation', '1': 'conversation',
                    'module': 'modules', 'cockpit': 'modules', 'agi': 'modules', '2': 'modules',
                    'agent': 'agents', '3': 'agents',
                    'avant-apres': 'avant_apres', 'avant_apres': 'avant_apres', 'gains': 'avant_apres', '4': 'avant_apres',
                    'log': 'journal', 'logs': 'journal', '5': 'journal',
                    'file': 'fichiers', 'files': 'fichiers', '6': 'fichiers',
                }
                resolved = alias_map.get(target, target)
                if resolved in self.PAGES:
                    self.page = resolved
                else:
                    cur = self.PAGES.index(self.page) if self.page in self.PAGES else 0
                    self.page = self.PAGES[(cur + 1) % len(self.PAGES)]
                self.refresh_body()
                return
            if cmd=='/agi':
                self.page = 'conversation'
                self.note(self.agi_cognitive_report(arg))
                return
            module_slash_map = {
                '/chat': 'CHAT',
                '/code': 'CODE',
                '/image': 'IMAGE',
                '/3d': '3D',
                '/cyber': 'CYBER',
                '/business': 'BUSINESS',
                '/academy': 'LEARNING',
                '/voice': 'VOICE',
            }
            if cmd in module_slash_map:
                mod_key = module_slash_map[cmd]
                self.active_module = mod_key
                meta = STUDIO_MODULES_META[mod_key]
                if not arg:
                    self.page = 'conversation'
                    self.note(f"### Module sélectionné · {mod_key}\n\n{meta['capabilities']}\n\n"
                              'Saisissez votre objectif. La disponibilité du moteur sera vérifiée à l’exécution.')
                    return
                raw = f"[{mod_key}] {arg}"
            elif cmd=='/missions':
                def listing():
                    with self.bridge_factory() as client:
                        return client.missions_list()
                result = await asyncio.to_thread(listing)
                self.note('\n'.join(f"{m['id']} · {m['status']} · {m['request'][:70]}" for m in result.get('missions',[])) or 'Aucune mission enregistrée.')
                return
            elif not raw.startswith(('/resume ','/attach ')):
                help_lines = ['# Commandes', '', 'Tapez / puis Tab pour compléter.', '']
                help_lines.extend(f'**{command}** — {description}\n' for command,_,_,description in SLASH_COMMANDS_CATALOG)
                help_lines.extend(['', 'F2 thèmes · F3 modèles · F4 vues · Alt+1..6 accès direct · F5 diagnostic',
                                   'Ctrl+C arrêt · Ctrl+Q sortie · Ctrl+N nouvelle conversation · Tab lecture',
                                   'Connexion : `jobia connect` dans le terminal.'])
                self.note('\n'.join(help_lines))
                return
        resume = raw.split(maxsplit=1)[1] if raw.startswith('/resume ') else None
        attach = raw.split(maxsplit=1)[1] if raw.startswith('/attach ') else None
        self.partial = ''
        self.state = MissionView(goal=raw,status='starting',phase='Reprise' if resume else 'Préparation',started=time.monotonic())
        self.state.record('Connexion · attente du serveur')
        self.ensure_clock()
        self.note('\n### VOUS\n\n'+raw)
        if self.active_skills and not resume and not attach:
            self.state.record('Skills joints · '+', '.join(self.active_skills))
        elif not resume and not attach and not pending:
            try:
                from . import skills as library
                hint = library.suggest(raw)
            except Exception:
                hint = None
            if hint is not None:
                self.show_toast(f'Skill pertinent : {hint.name} · /skill {hint.name} pour l’activer', 'accent', 10)
        if not resume and not attach and not pending:
            self.history.append({'role':'user','content':raw})
            if hasattr(self.history,'save'):
                self.history.save()
        try:
            mode = config.get('mode','auto')
            remote = bool(pending or attach or resume or mode=='remote')
            fallback = False
            if not remote and mode=='auto' and config.is_configured():
                from .routing import remote_readiness
                remote, reason = await asyncio.to_thread(remote_readiness,self.bridge_factory)
                if not remote:
                    fallback = True
                    self.note('PC fixe indisponible · '+plain(reason)+'\nExécution sur cet ordinateur avec les moteurs locaux disponibles.')
            if remote:
                self._active_target = target_label(config.resolve_server_url())
                self.note('Exécution sur le serveur · '+self._active_target)
                if not attach and not resume and not pending:
                    from urllib.parse import urlsplit
                    local_server = urlsplit(config.resolve_server_url()).hostname in {'127.0.0.1','localhost','::1'}
                    pending = {'request':self.request_text(raw),'workspace':str(Path.cwd()) if local_server else '',
                               'permissions':config.get('default_permissions','SAFE'),
                               'model':config.get('default_model',''),'idempotency_key':uuid4().hex,
                               'history':[{'role':m['role'],'content':m['content'][:3000]+('\n[historique tronqué]' if len(m['content'])>3000 else '')}
                                          for m in self.history[:-1][-10:]]}
                    if hasattr(self.history,'state'):
                        self.history.state['pending_request'] = pending
                        self.history.save()
                await asyncio.to_thread(self._remote,raw,resume=resume,attach=attach,pending=pending)
            else:
                if fallback:
                    await self.local(raw,automatic=True)
                else:
                    await self.local(raw)
        except Exception as exc:
            self.event({'type':'error','message':str(exc) or type(exc).__name__})
        finally:
            self.app.invalidate()

    async def local(self,raw,*,automatic=False):
        self._active_target = 'cet ordinateur'
        self.connection = 'local'
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
        payload = {'request':self.request_text(raw),'history':list(self.history[:-1]),'model':config.get('default_model',''),
                   'allow_model_selection':automatic}
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
        mission = result.get('mission', {})
        if mission:
            self.event({'type':'mission_snapshot', **mission})
            for path, digest in mission.get('files', {}).items():
                if digest:
                    self.event({'type':'file_available', 'path':path})
        if not result.get('ok'):
            self.event({'type':'error','message':result.get('error','Le résultat local demandé n’est pas vérifié.')})
            return
        self.event({'type':'mission_complete','result':result.get('result',''), 'status':'completed',
                    'verification':{'verified':list(mission.get('verified',{}))}})

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

    def show_recovery_notices(self):
        """Shared startup contract for both renderers; never dispatch work."""
        if self._recovery_notices_shown:
            return
        self._recovery_notices_shown = True
        active = getattr(self.history,'state',{}).get('active_mission_id')
        if active:
            self.note('Mission conservée : '+active+' · /attach '+active+' pour suivre son état.',reveal=False)
        if getattr(self.history,'state',{}).get('pending_request'):
            if retry_payload_valid(self.history.state['pending_request']):
                self.note('Une demande a une acceptation incertaine. /retry conserve sa clé pour éviter une double exécution.',reveal=False)
            else:
                self.note('Une demande conservée a un format de reprise incomplet. /retry permet de la récupérer sans la lancer.',reveal=False)

    async def run(self):
        self.loop = asyncio.get_running_loop()
        self.show_recovery_notices()
        def start():
            self.app.create_background_task(self.discover())
            self.app.create_background_task(self.monitor())
            self.ensure_clock()
        try:
            await self.app.run_async(pre_run=start)
        finally:
            for handle in (self._clock_handle,self._refresh_handle):
                if handle is not None:
                    handle.cancel()

    def ticking(self):
        """Real activity worth a live clock: a mission, a reconnection, a pairing."""
        return (self.state.status in {'starting','running','queued','permission'}
                or self.connection in {'diagnostic','reconnexion','hors ligne','appairage'})

    def ensure_clock(self):
        """A one-second status clock exists only while something really happens."""
        if not self.ticking():
            if self._clock_handle is not None:
                self._clock_handle.cancel()
                self._clock_handle = None
            return
        if self._clock_handle is None:
            try:
                self._clock_handle = asyncio.get_running_loop().call_later(1,self._clock_tick)
            except RuntimeError:
                pass

    def _clock_tick(self):
        self._clock_handle = None
        if self.ticking():
            self.app.invalidate()
            self.ensure_clock()


def run_ui(renderer=None):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        from .workspace import dashboard
        dashboard()
        display.hint('Ouvre un terminal interactif pour l’interface ; jobia --help pour les commandes.')
        return
    from .renderer import launch_native
    if not launch_native(renderer):
        asyncio.run(WorkspaceApp().run())
