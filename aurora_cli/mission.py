"""Mission tracking and progress display for the remote bridge."""
from __future__ import annotations
import os
import subprocess
import time
from uuid import uuid4
from pathlib import Path
from urllib.parse import urlsplit

from aurora_cli import display
from aurora_cli.bridge import Bridge
from aurora_cli.transfers import receive_file
from aurora_cli.observations import observation_text
from aurora_cli.terminal import target_label


def _safe_target(root: Path, raw: str, for_write: bool = False) -> Path | None:
    """Resolve a server-supplied path inside the workspace, reject traversal."""
    if (not isinstance(raw, str) or not raw or "\x00" in raw
            or "\\" in raw or ":" in raw or urlsplit(raw).scheme):
        return None
    if os.path.isabs(raw):
        target = Path(raw)
    elif any(part in ("", ".", "..") for part in raw.split("/")):
        return None
    else:
        target = root.joinpath(*raw.split("/"))
    if target.is_symlink():
        return None
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        return None
    if for_write:
        resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved


def run_mission(client: Bridge, request: str, workspace: str = "", permissions: str = "AUTONOMOUS", model: str = "", session_id: str = "", *, server_workspace: str | None = None, resume_id: str = "", watch_id: str = "", context_tokens: int | None = None, dispatch_state=None, input_files=()) -> bool:
    """Start and monitor an autonomous mission."""
    if server_workspace is None:
        local_server = urlsplit(client.server_url).hostname in ("localhost", "127.0.0.1", "::1")
        server_workspace = workspace if local_server else ""
    try:
        options = {'context_tokens':context_tokens} if context_tokens is not None else {}
        retained = getattr(dispatch_state, 'state', None)
        payload = dict(request=request, workspace=server_workspace, permissions=permissions,
                       model=model, session_id=session_id, idempotency_key=uuid4().hex, **options)
        if isinstance(retained, dict) and not resume_id and not watch_id:
            pending = retained.get('pending_request')
            active = retained.get('active_mission_id')
            if pending or active:
                if retained.get('mission_server_url') != client.server_url:
                    raise ValueError('Serveur différent pour une acceptation retenue ; aucune nouvelle mission créée')
                original = pending or retained.get('accepted_request', {})
                if original.get('request') != request:
                    raise ValueError('Mission précédente retenue ; suivre/reprendre son identifiant avant une nouvelle demande')
                if active:
                    watch_id = active
                else:
                    payload = dict(pending)
            else:
                if isinstance(client, Bridge):
                    payload['references'] = client.prepare_references(request, input_files)
                retained['pending_request'] = payload
                retained['mission_server_url'] = client.server_url
                dispatch_state.save()
        elif not resume_id and not watch_id and isinstance(client, Bridge):
            payload['references'] = client.prepare_references(request, input_files)
        data = (client.mission_status(watch_id) if watch_id else client.mission_resume(resume_id,model=model) if resume_id else
                client.mission_start(**payload))
        if watch_id and data.get("ok"):
            data.update(mission_id=watch_id,cursor=data.get("stream_start_cursor",0))
        mission_id = data.get("mission_id")
        if mission_id and isinstance(retained, dict):
            retained.update(active_mission_id=mission_id, accepted_request=payload)
            retained.pop('pending_request', None)
            dispatch_state.save()
        if not mission_id:
            display.error(data.get("error", "Impossible de démarrer la mission."))
            return False
        display.hint('Mission distante retenue : '+mission_id)
    except Exception as e:
        display.error(f"Erreur de démarrage: {e}")
        return False

    actual_ws = workspace or os.getcwd()
    display.header('MISSION DISTANTE', request)
    display.print(display.view.key_value_table('CONNEXION', [
        ('Cible', target_label(client.server_url)), ('Mission', mission_id),
        ('Session', session_id or 'session du serveur'),
        ('Permissions', permissions), ('Réception des fichiers', actual_ws),
        ('Exécution', server_workspace or 'espace de travail du pont'),
    ]))
    display.info('Mission acceptée · attente du premier événement')

    current_step = ""
    tokens_printed = False

    live_spinner = None
    reflection_start = 0

    def _separate_from_tokens() -> None:
        nonlocal tokens_printed
        if tokens_printed:
            display.view.stream("\n")
            tokens_printed = False

    try:
        for event in client.mission_stream(mission_id, last_event_id=data.get("cursor", 0)):
            etype = event.get("type", "")
            
            if etype == "step_start":
                step_name = event.get("step", "")
                current_step = step_name
                _separate_from_tokens()
                display.info(step_name)
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                if display.view.caps.is_tty and display.view.caps.animate:
                    reflection_start = time.monotonic()
                    live_spinner = display.thinking(step_name, reflection_start)
                    live_spinner.__enter__()

            elif etype == "step_end":
                step_name = event.get("step", current_step)
                if live_spinner and ("Réflexion" in step_name or "Exécution" in step_name):
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                    elapsed_step = int(time.monotonic() - reflection_start)
                    display.hint(f"{step_name} · {elapsed_step}s")
                elif "Action:" in step_name:
                    pass # Handled by the tokens usually, or we can ignore
                
            elif etype == "heartbeat":
                if live_spinner:
                    live_spinner.update()

            elif etype == "tool_start":
                _separate_from_tokens()
                display.info('Outil en cours : '+str(event.get('tool', 'outil')))

            elif etype in {'plan', 'plan_revision'}:
                _separate_from_tokens()
                display.header('Plan reçu' if etype == 'plan' else 'Plan révisé')
                for index, step in enumerate(event.get('steps', []), 1):
                    display.kv(str(index), step)

            elif etype == "token":
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                token = event.get("content", "")
                # Custom formatting for Bash commands
                if "[EXECUTION BASH]:" in token:
                    cmd = token.replace("[EXECUTION BASH]:", "").strip()
                    display.info(f"Commande annoncée par le serveur : {cmd}")
                else:
                    display.view.stream(token)
                    tokens_printed = True
                
            elif etype in {'environment_observation','tool_result','command_output','recovery_observation','completion_observation',
                           'stagnation_notice','recovery_start','recovery_proposal','recovery_rejected','review_result'}:
                detail = observation_text(event, 1200)
                if detail:
                    if live_spinner:
                        live_spinner.__exit__(None, None, None)
                        live_spinner = None
                    _separate_from_tokens()
                    label = ('Outil · '+str(event.get('tool', ''))+' · '+('réussi' if event.get('ok') else 'échec')) if etype=='tool_result' else 'Observation'
                    display.header(label)
                    display.print(display.view.text(detail))

            elif etype == "file_diff":
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                _separate_from_tokens()
                filename = event.get("filename", "unknown")
                diff_lines = event.get("diff", [])
                display.code_diff(filename, diff_lines)
                
            elif etype == "sudo_request":
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                _separate_from_tokens()
                reason = event.get("reason", "Action nécessite des privilèges élevés")
                pwd = display.ask_password(f"{reason}. Mot de passe sudo :")
                try:
                    client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "password", "value": pwd})
                    display.success("Mot de passe transmis et supprimé localement.")
                except Exception as e:
                    display.error(f"Erreur d'envoi du mot de passe : {e}")

            elif etype == "file_transfer":
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                _separate_from_tokens()
                root = Path(workspace or os.getcwd()).resolve()
                out_path = receive_file(event, client, root)
                display.success(f"Fichier reçu et vérifié : {out_path}")

            elif etype == "remote_command":
                cmd = event.get("command", "")
                cwd = event.get("cwd", "")
                display.info(f"Exécution locale (client) : {cmd}")
                try:
                    root = Path(workspace or os.getcwd()).resolve()
                    safe_cwd = _safe_target(root, cwd) if cwd else root
                    if safe_cwd is None:
                        out = f"Chemin local refusé (hors espace de travail) : {cwd}"
                    else:
                        # iter32 SEC: exécution directe (sans shell) quand la
                        # commande ne contient AUCUN opérateur shell — elle ne
                        # passe donc pas par /bin/sh (pas d'injection de ; | &
                        # ni de glob). Uniquement les commandes composées
                        # (|, &&, ||, ;, >, <, $, `) passent par le shell,
                        # strictement dans l'espace de travail vérifié.
                        _shell_meta = any(op in cmd for op in ("|", "&&", "||", ";", ">", "<", "$(", "`"))
                        if not _shell_meta:
                            import shlex
                            _parts = shlex.split(cmd)
                            result = subprocess.run(_parts, cwd=str(safe_cwd), capture_output=True, text=True, timeout=120)
                        else:
                            result = subprocess.run(cmd, shell=True, cwd=str(safe_cwd), capture_output=True, text=True, timeout=120)
                        out = result.stdout + "\n" + result.stderr
                        if not out.strip():
                            out = "Commande réussie sans sortie."
                except Exception as e:
                    out = f"Erreur d'exécution locale: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_command_result", "value": out})

            elif etype == "remote_read_file":
                path = event.get("path", "")
                display.info(f"Lecture locale (client) : {path}")
                try:
                    root = Path(workspace or os.getcwd()).resolve()
                    safe_path = _safe_target(root, path)
                    if safe_path is None:
                        out = f"Chemin local refusé (hors espace de travail) : {path}"
                    else:
                        with open(safe_path, "r", encoding="utf-8") as f:
                            out = f.read()
                except Exception as e:
                    out = f"Erreur de lecture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_read_result", "value": out})

            elif etype == "remote_write_file":
                path = event.get("path", "")
                content = event.get("content", "")
                display.info(f"Écriture locale (client) : {path}")
                try:
                    root = Path(workspace or os.getcwd()).resolve()
                    safe_path = _safe_target(root, path, for_write=True)
                    if safe_path is None:
                        out = f"Chemin local refusé (hors espace de travail) : {path}"
                    else:
                        with open(safe_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        out = "Fichier écrit avec succès sur le client."
                except Exception as e:
                    out = f"Erreur d'écriture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_write_result", "value": out})

            elif etype == "reconnecting":
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                _separate_from_tokens()
                display.warning(f"Connexion interrompue · reprise du flux {event.get('attempt', '?')}/3 · mission {mission_id}")
                display.hint('Le serveur conserve la mission ; aucune nouvelle demande envoyée.')

            elif etype in ("error", "mission_interrupted"):
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                display.error(event.get("message") or event.get("error", "Une erreur est survenue."))
                return False
                
            elif etype == "mission_complete":
                if live_spinner:
                    live_spinner.__exit__(None, None, None)
                    live_spinner = None
                _separate_from_tokens()
                completed = event.get('status', 'completed') == 'completed' and not event.get('stopped')
                if completed:
                    display.mission_summary(event)
                else:
                    display.warning('Mission '+str(event.get('status', 'arrêtée'))+' · travail conservé')
                    if event.get('result'):
                        display.print(display.view.markdown(str(event['result']), title='Résultat reçu'))
                if isinstance(retained, dict) and event.get('status','completed') == 'completed' and not event.get('stopped'):
                    retained['last_completed_mission_id'] = mission_id
                    retained.pop('active_mission_id', None)
                    retained.pop('accepted_request', None)
                    dispatch_state.save()
                return not event.get("stopped", False) and event.get("status", "completed") == "completed"

        display.error("Flux fermé sans confirmation de fin de mission.")
        return False
                
    except KeyboardInterrupt:
        display.view.stream("\n")
        display.info("Interruption demandée, arrêt de la mission sur le serveur...")
        try:
            result = client.mission_stop(mission_id)
            if result.get("ok"):
                display.info("Arrêt demandé." if result.get("status") == "stopping" else "Mission terminée.")
            else:
                display.error(result.get("error", "Arrêt de la mission non confirmé."))
        except Exception as e:
            display.error(f"Erreur lors de l'arrêt: {e}")
        return False
    except Exception as e:
        display.error(f"Mission interrompue : {e}")
        return False
    finally:
        if live_spinner:
            live_spinner.__exit__(None, None, None)


def handle_temporary_agents(client: Bridge, agents: list[dict]) -> None:
    """Prompt user to save or delete temporary agents created during the mission."""
    display.header(f"Agents temporaires créés : {len(agents)}")
    for a in agents:
        display.kv(a.get("name", "Agent"), a.get("role", "Agent"))
    
    display.hint("[S] Tout sauvegarder")
    display.hint("[s] Tout supprimer")
    display.hint("[c] Choisir individuellement")
    
    choice = display.view.console.input(display.mark("accent", "Choix (S/s/c) [s] : ")).strip()
    
    if choice == 'S':
        for a in agents:
            client.agent_dynamic_save(a['id'])
        display.success(f"{len(agents)} agents sauvegardés de façon permanente.")
    elif choice == 'c':
        for a in agents:
            keep = display.confirm(f"Sauvegarder {a.get('name')} ?", default=False)
            if keep:
                client.agent_dynamic_save(a['id'])
                display.success(f"Sauvegardé : {a.get('name')}")
            else:
                client.agent_dynamic_delete(a['id'])
                display.info(f"Supprimé : {a.get('name')}")
    else:
        # Default behavior is to delete
        for a in agents:
            client.agent_dynamic_delete(a['id'])
        display.info("Tous les agents temporaires ont été supprimés.")
