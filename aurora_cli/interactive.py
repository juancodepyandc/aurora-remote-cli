"""Interactive REPL for the remote bridge."""
from __future__ import annotations
import signal
import time

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter

from aurora_cli import brand, config, display
from aurora_cli.core import locations
from aurora_cli.bridge import Bridge
try:
    from aurora_cli.core.jobia import JOBIACore
    jobia_engine = JOBIACore()
except ImportError:
    jobia_engine = None


INTERNAL_COMMANDS = {
    '/help': 'Toutes les commandes de cette session',
    '/agi': 'Inspecter l’analyse de la demande',
    '/modules': 'Catalogue des modules déclarés',
    '/chat': 'Choisir le module Conversation',
    '/code': 'Choisir le module Code',
    '/image': 'Choisir le module Image',
    '/3d': 'Choisir le module 3D',
    '/cyber': 'Choisir le module Cyber',
    '/business': 'Choisir le module Business',
    '/academy': 'Choisir le module Académie',
    '/voice': 'Choisir le module Voix',
    '/status': 'État observé du pont',
    '/permissions': 'Voir ou régler les permissions',
    '/agents': 'Agents du pont',
    '/tools': 'Outils du pont',
    '/models': 'Modèles du pont',
    '/local': 'Modèles présents sur cette machine',
    '/mcp': 'Serveurs MCP et outils',
    '/skills': 'Skills du pont',
    '/connections': 'Connexions aux services',
    '/session': 'Identifiant de la session',
    '/sessions': 'Lister, reprendre ou supprimer une session',
    '/fresh': 'Nouvelle session distante',
    '/theme': 'Thème de cette session',
    '/clear': 'Effacer le terminal',
    '/stop': 'Demander l’arrêt de la mission',
    '/exit': 'Quitter',
    '/mode': 'Mode d’exécution et modèle demandé',
}

COMMAND_COMPLETER = WordCompleter(
    list(INTERNAL_COMMANDS.keys()),
    sentence=True,
    meta_dict=INTERNAL_COMMANDS,
)


def run_interactive(client: Bridge) -> None:
    """Linear remote session using the same components as all CLI commands."""
    from prompt_toolkit.styles import Style
    from aurora_cli.terminal import target_label

    measured = time.monotonic()
    try:
        status = client.status()
        latency_ms = (time.monotonic() - measured) * 1000
        display.banner(animate=False)
        display.header('SESSION DISTANTE', target_label(client.server_url))
        if status.get('ok') is False:
            display.warning(status.get('error', 'Le pont signale un état dégradé.'))
    except Exception as exc:
        display.error(f'Pont injoignable : {exc}')
        display.hint(f'{brand.APP_COMMANDS[0]} connect pour enregistrer un pont distant.')
        return

    session_id = ''
    session_permissions = config.get('default_permissions', 'AUTONOMOUS')
    try:
        sessions = client.session_list().get('sessions', [])
        if sessions:
            display.print(display.view.table('REPRENDRE UNE SESSION',
                [('Choix', 'primary'), ('Session', 'text'), ('Permissions', 'muted')],
                [(str(i), item['id'], item.get('permissions', 'non précisées'))
                 for i, item in enumerate(sessions, 1)]))
            answer = display.view.console.input(display.mark('accent', 'Numéro, ou Entrée pour une nouvelle session : ')).strip()
            if answer.isdigit() and 1 <= int(answer) <= len(sessions):
                session_id = sessions[int(answer)-1]['id']
                session_permissions = sessions[int(answer)-1].get('permissions', 'non précisées')
                display.success('Session reprise : '+session_id)
        if not session_id:
            data = client.session_create(permissions=config.get('default_permissions', 'AUTONOMOUS'))
            session_id = data.get('session', {}).get('id', '')
            if session_id:
                display.success('Nouvelle session : '+session_id)
    except Exception as exc:
        display.warning(f'Session non confirmée : {exc}')

    locations.ensure_dirs()
    prompt_session = PromptSession(history=FileHistory(str(locations.history_file())),
                                   completer=COMMAND_COMPLETER, complete_while_typing=True)
    current_mission_id = ''
    active_repl_module = ''

    def signal_handler(sig, frame):
        nonlocal current_mission_id
        if current_mission_id:
            try:
                result = client.mission_stop(current_mission_id)
                if result.get('ok'):
                    display.info('Arrêt demandé au serveur.')
                else:
                    display.error(result.get('error', 'Arrêt non confirmé.'))
            except Exception as exc:
                display.error(f'Arrêt non confirmé : {exc}')
        else:
            display.hint('Ctrl+C pour interrompre, /exit pour quitter.')

    previous_signal = signal.signal(signal.SIGINT, signal_handler)
    display.hint('Décris ta demande · / pour les commandes · /exit pour quitter')
    try:
        while True:
            try:
                view = display.view
                prompt_session.style = Style.from_dict(view.theme.prompt_toolkit_style(view.caps))
                context = (f'{target_label(client.server_url)} · session {session_id or "non confirmée"}'
                           f' · {latency_ms:.0f} ms au diagnostic · {session_permissions}')
                user_input = prompt_session.prompt(
                    [('class:prompt', view.text((active_repl_module+' · ' if active_repl_module else '')+view.theme.prompt_text(view.caps)))],
                    bottom_toolbar=view.text(context)).strip()
            except (EOFError, KeyboardInterrupt):
                display.info('À bientôt.')
                break
            if not user_input:
                continue
            if user_input.startswith('/'):
                parts = user_input.split(maxsplit=1)
                cmd, arg = parts[0].lower(), parts[1].strip() if len(parts) > 1 else ''
                if cmd in {'/exit', '/quit'}:
                    display.info('À bientôt.')
                    break
                if cmd in {'/help', '/?'}:
                    _show_help()
                elif cmd == '/modules':
                    from aurora_cli.ui import STUDIO_MODULES_META
                    display.print(view.table('MODULES DÉCLARÉS',
                        [('Module', 'primary'), ('Rôle', 'secondary'), ('Description', 'text')],
                        [(key, meta['master'], meta['engine']+'\n'+meta['capabilities'])
                         for key, meta in STUDIO_MODULES_META.items()]))
                    display.hint('Catalogue déclaré ; disponibilité réelle à vérifier avec /status.')
                elif cmd == '/agi':
                    from aurora_cli.ui import WorkspaceApp
                    diagnostic = WorkspaceApp.__new__(WorkspaceApp)
                    diagnostic.active_module = active_repl_module
                    diagnostic.state = type('State', (), {'goal': arg or 'Diagnostic'})()
                    display.print(view.markdown(diagnostic.agi_cognitive_report(arg), title='Analyse de la demande'))
                elif cmd in {'/chat', '/code', '/image', '/3d', '/cyber', '/business', '/academy', '/voice'}:
                    active_repl_module = {'/academy': 'LEARNING'}.get(cmd, cmd[1:].upper())
                    if not arg:
                        display.success('Module choisi : '+active_repl_module)
                        continue
                    user_input = '['+active_repl_module+'] '+arg
                elif cmd == '/status':
                    _cmd_status(client)
                elif cmd == '/permissions':
                    applied = _cmd_permissions(client, user_input)
                    if applied:
                        session_permissions = applied
                elif cmd == '/agents':
                    _cmd_agents(client)
                elif cmd == '/tools':
                    _cmd_tools(client)
                elif cmd == '/models':
                    _cmd_models(client)
                elif cmd == '/local':
                    _cmd_local_models()
                elif cmd == '/theme':
                    _cmd_theme(view.theme)
                elif cmd == '/mcp':
                    _cmd_mcp(client)
                elif cmd == '/skills':
                    _cmd_skills(client)
                elif cmd == '/connections':
                    _cmd_connections(client)
                elif cmd == '/session':
                    display.kv('Session', session_id or 'non confirmée')
                elif cmd == '/sessions':
                    session_id = _cmd_sessions(client, session_id)
                elif cmd == '/fresh':
                    try:
                        data = client.session_create(permissions=config.get('default_permissions', 'AUTONOMOUS'))
                        session_id = data.get('session', {}).get('id', '')
                        display.success('Nouvelle session : '+session_id) if session_id else display.warning('Session non confirmée.')
                    except Exception as exc:
                        display.error(f'Erreur de session : {exc}')
                elif cmd == '/clear':
                    view.clear()
                elif cmd == '/mode':
                    _cmd_mode()
                elif cmd == '/stop':
                    if current_mission_id:
                        signal_handler(None, None)
                    else:
                        display.hint('Aucune mission en cours.')
                else:
                    display.warning(f'Commande inconnue : {cmd}. /help pour les commandes.')
                if user_input.startswith('/'):
                    continue
            if jobia_engine is None:
                display.error('Moteur JOBIA indisponible. jobia doctor pour le diagnostic.')
                continue

            try:
                import threading
                import queue
                done = threading.Event()
                first = threading.Event()
                chunks = queue.Queue()
                result = {'text': '', 'status': 'error', 'reconnecting': False, 'files': []}

                def received(task_id, payload):
                    nonlocal current_mission_id
                    if isinstance(payload, dict) and payload.get('mission_id'):
                        current_mission_id = payload['mission_id']
                    if isinstance(payload, dict) and payload.get('reconnecting'):
                        result['reconnecting'] = True
                    elif isinstance(payload, dict) and payload.get('file_transfer'):
                        result['files'].append(payload)
                    elif isinstance(payload, dict) and payload.get('stream'):
                        result['reconnecting'] = False
                        chunks.put(str(payload['data']))
                        first.set()
                    elif isinstance(payload, dict) and (payload.get('stream_done') or 'data' in payload):
                        result['status'] = payload.get('status', 'error')
                        data = payload.get('data', '')
                        result['text'] = str(data.get('data', data) if isinstance(data, dict) else data)
                        done.set()
                    else:
                        result['text'] = str(payload)
                        done.set()
                    if done.is_set():
                        current_mission_id = ''

                display.info('Transmission de la demande au PC · attente de confirmation')
                started = time.monotonic()
                jobia_engine.process_request(user_input, callback=received, client=client, session_id=session_id)
                with display.thinking('Attente d’événements du serveur', started) as thinking:
                    was_reconnecting = False
                    while not done.is_set() and not first.is_set():
                        if result['reconnecting'] and not was_reconnecting:
                            display.warning('Connexion interrompue · reprise du même flux distant')
                        was_reconnecting = result['reconnecting']
                        thinking.update()
                        done.wait(.1)
                if first.is_set():
                    display.header('RÉPONSE EN COURS')
                    while not done.is_set() or not chunks.empty():
                        try:
                            view.stream(chunks.get(timeout=.1))
                        except queue.Empty:
                            pass
                    view.stream('\n')
                elif result['text']:
                    display.print(view.markdown(result['text'], title='Réponse'))
                else:
                    display.warning('Aucune réponse reçue.')
                if result['files']:
                    display.files_received(result['files'])
                outcome = result['status']
                label = 'Mission accomplie' if outcome == 'success' else 'Mission arrêtée' if outcome == 'stopped' else 'Mission interrompue ou échouée'
                report = display.success if outcome == 'success' else display.warning if outcome == 'stopped' else display.error
                report(f'{label} · {time.monotonic()-started:.1f}s')
            except KeyboardInterrupt:
                display.warning('Interrompu par l’utilisateur.')
            except Exception as exc:
                display.error(f'Erreur d’exécution : {exc}')
    finally:
        signal.signal(signal.SIGINT, previous_signal)


def _cmd_mode():
    if jobia_engine is None:
        display.error('Moteur JOBIA indisponible.')
        return
    modes = list(jobia_engine.MODE_TO_MODEL)
    display.print(display.view.table('MODE D’EXÉCUTION',
        [('Choix', 'primary'), ('Mode', 'text'), ('Modèle demandé', 'muted')],
        [(str(index), mode, jobia_engine.MODE_TO_MODEL[mode] or 'Défaut du serveur')
         for index, mode in enumerate(modes, 1)]))
    raw = display.view.console.input(display.mark('accent', 'Mode (numéro ou nom, Entrée pour conserver) : ')).strip()
    if not raw:
        display.hint('Mode conservé : '+jobia_engine.mode)
        return
    selected = modes[int(raw)-1] if raw.isdigit() and 1 <= int(raw) <= len(modes) else raw.lower()
    if jobia_engine.set_mode(selected):
        display.success(f'Mode : {selected} · modèle : {jobia_engine.get_model() or "défaut du serveur"}')
    else:
        display.error('Mode non reconnu.')


def _show_help() -> None:
    display.print(display.view.table('COMMANDES', [('Commande', 'primary'), ('Usage', 'text')], INTERNAL_COMMANDS.items()))


def _cmd_status(client: Bridge) -> None:
    try:
        data = client.status()
        display.status_display(data)
    except Exception as e:
        display.error(f"Erreur: {e}")


def _cmd_permissions(client, user_input: str) -> None:
    known = config.PERMISSION_LEVELS
    parts = user_input.split()
    if len(parts) > 1:
        level = parts[1].upper()
    else:
        try:
            data = client.permissions_get()
            display.permissions_display(data.get("levels", {}))
            display.hint('Entrée pour conserver les permissions actuelles.')
            level = display.view.console.input(display.mark('accent', 'Nouvelle permission : ')).strip().upper()
            if not level:
                return
        except Exception as e:
            display.error(str(e))
            return

    if level not in known:
        display.error(f"Niveau inconnu : {level}. Niveaux autorisés : {', '.join(known)}")
        return

    try:
        result = client.permissions_set(level)
        if result.get("ok"):
            display.success(f"Permissions: {level}")
            return level
        else:
            display.error(result.get("error", "Failed"))
    except Exception as e:
        display.error(str(e))


def _cmd_agents(client: Bridge) -> None:
    try:
        off = client.agents_official()
        dyn = client.agents_dynamic()
        display.agents_table(off.get("agents", []), dyn.get("agents", []))
    except Exception as e:
        display.error(str(e))


def _cmd_tools(client: Bridge) -> None:
    try:
        data = client.tools()
        display.tools_table(data.get("tools", []))
    except Exception as e:
        display.error(str(e))


def _cmd_models(client: Bridge) -> None:
    try:
        data = client.models()
        rows = data.get('models', [])
        if not rows:
            display.hint('Aucun modèle annoncé par le pont.')
        else:
            display.print(display.view.table('MODÈLES DU PONT', [('Modèle', 'primary'), ('Source', 'muted')],
                [(row.get('name', row.get('id', 'non précisé')), row.get('provider', 'serveur'))
                 if isinstance(row, dict) else (str(row), 'serveur') for row in rows]))
    except Exception as e:
        display.error(str(e))


def _cmd_local_models() -> None:
    """List the models this machine can serve, without contacting the bridge."""
    from aurora_cli.core.discovery import scan

    display.hint("Analyse de cette machine…")
    result = scan(deep=True)
    display.providers_table(result.providers)
    loose = result.loose_models
    if loose:
        display.hint("")
        display.models_table(loose)
    if not result.any_provider and not loose:
        display.hint("Aucun runtime local détecté. Lancez Ollama, LM Studio ou llama-server.")


def _cmd_theme(theme) -> None:
    """Switch theme for this session only, without touching the config file."""
    from aurora_cli import themes

    display.hint(themes.describe())
    choice = display.view.console.input(display.mark("accent", "Thème : ")).strip()
    if not choice:
        return
    if not themes.is_known(choice):
        display.error(f"Thème inconnu : {choice}")
        return
    new_id, origin = display.view.use_theme(choice)
    display.success(f"Thème de session : {new_id} ({origin})")
    display.hint("Il ne sera pas enregistré. Utilisez jobia theme set NOM pour le rendre permanent.")


def _cmd_mcp(client: Bridge) -> None:
    try:
        servers = client.mcp_list()
        tools = client.mcp_tools()
        display.mcp_table(servers.get("servers", []), tools.get("tools", []))
    except Exception as e:
        display.error(str(e))


def _cmd_skills(client: Bridge) -> None:
    try:
        data = client.skills_list()
        display.skills_table(data.get("skills", []))
    except Exception as e:
        display.error(str(e))


def _cmd_connections(client: Bridge) -> None:
    try:
        data = client.connections_list()
        display.connections_table(data.get("connections", []))
    except Exception as e:
        display.error(str(e))


def _cmd_sessions(client: Bridge, current_session_id: str = '') -> str:
    try:
        sessions = client.session_list().get('sessions', [])
        if not sessions:
            display.hint('Aucune session active.')
            return current_session_id
        display.sessions_table(sessions)
        display.hint('Entrée pour conserver la session · reprendre ID · supprimer ID')
        action = display.view.console.input(display.mark('accent', 'Session : ')).strip().split(maxsplit=1)
        if len(action) != 2:
            return current_session_id
        operation, sid = action
        if sid not in {item['id'] for item in sessions}:
            display.error('Session inconnue : '+sid)
        elif operation in {'reprendre', 'resume'}:
            display.success('Session reprise : '+sid)
            return sid
        elif operation in {'supprimer', 'delete'} and display.confirm('Supprimer la session '+sid+' ?', default=False):
            result = client.session_delete(sid)
            if result.get('ok'):
                display.success('Session supprimée : '+sid)
                if sid == current_session_id:
                    display.warning('Session actuelle supprimée. /fresh pour en créer une nouvelle.')
                    return ''
            else:
                display.error(result.get('error', 'Suppression non confirmée.'))
    except Exception as exc:
        display.error(str(exc))
    return current_session_id
