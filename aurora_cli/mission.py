"""Mission tracking and display for Aurora CLI."""
from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit

from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from aurora_cli import display
from aurora_cli.client import AuroraClient
from aurora_cli.transfers import receive_file


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


def run_mission(client: AuroraClient, request: str, workspace: str = "", permissions: str = "AUTONOMOUS", model: str = "", session_id: str = "", *, server_workspace: str | None = None) -> bool:
    """Start and monitor an autonomous mission."""
    if server_workspace is None:
        local_server = urlsplit(client.server_url).hostname in ("localhost", "127.0.0.1", "::1")
        server_workspace = workspace if local_server else ""
    try:
        data = client.mission_start(request, workspace=server_workspace, permissions=permissions, model=model, session_id=session_id)
        mission_id = data.get("mission_id")
        if not mission_id:
            display.error(data.get("error", "Impossible de démarrer la mission."))
            return False
    except Exception as e:
        display.error(f"Erreur de démarrage: {e}")
        return False

    from rich.panel import Panel

    actual_ws = workspace or os.getcwd()
    
    info_text = Text()
    info_text.append("🚀 Mission : ", style="bold green")
    
    # Clean up request for display
    display_req = request.replace('\n', ' ')
    if len(display_req) > 55:
        display_req = display_req[:55] + "..."
        
    info_text.append(f"{display_req}\n")
    info_text.append("Réception des fichiers : ", style="bold blue")
    info_text.append(f"{actual_ws}\n")
    info_text.append("Exécution sur le serveur : ", style="bold magenta")
    info_text.append(f"{server_workspace or 'espace de travail Aurora'} - Permissions: {permissions}\n\n")
    info_text.append("Analyse de la requête en cours...", style="dim italic")
    
    display.console.print(Panel(info_text, title="[bold cyan]✧ Aurora-IA Initialisation[/bold cyan]", border_style="cyan"))

    current_step = ""
    start_time = time.time()
    tokens_printed = False

    live_spinner = None
    reflection_start = 0

    def _separate_from_tokens() -> None:
        nonlocal tokens_printed
        if tokens_printed:
            display.console.print("\n")
            tokens_printed = False

    try:
        for event in client.mission_stream(mission_id):
            etype = event.get("type", "")
            
            if etype == "step_start":
                step_name = event.get("step", "")
                current_step = step_name
                if "Réflexion" in step_name or "Exécution" in step_name:
                    if live_spinner:
                        live_spinner.stop()
                    reflection_start = time.time()
                    spin = Spinner("bouncingBar", text=Text(f"✧ Aurora | {step_name}...", style="bold magenta"))
                    live_spinner = Live(spin, refresh_per_second=10, console=display.console, transient=True)
                    live_spinner.start()
                
            elif etype == "step_end":
                step_name = event.get("step", current_step)
                if live_spinner and ("Réflexion" in step_name or "Exécution" in step_name):
                    live_spinner.stop()
                    live_spinner = None
                    elapsed_step = int(time.time() - reflection_start)
                    display.console.print(f"[bold magenta]✧ Aurora[/bold magenta] [dim]| {step_name} ({elapsed_step}s)[/dim]")
                elif "Action:" in step_name:
                    pass # Handled by the tokens usually, or we can ignore
                
            elif etype == "heartbeat":
                if live_spinner:
                    elapsed = event.get("elapsed")
                    try:
                        elapsed_secs = int(elapsed) if elapsed is not None else 0
                    except (TypeError, ValueError):
                        elapsed_secs = 0
                    mins = int(elapsed_secs) // 60
                    secs = int(elapsed_secs) % 60
                    spin = Spinner("bouncingBar", text=Text(f"✧ Aurora | {current_step} [{mins:02d}:{secs:02d}]...", style="bold magenta"))
                    live_spinner.update(spin)
                    
            elif etype == "token":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                token = event.get("content", "")
                # Custom formatting for Bash commands
                if "[EXECUTION BASH]:" in token:
                    cmd = token.replace("[EXECUTION BASH]:", "").strip()
                    display.console.print(f"\n[bold cyan]⚡[/bold cyan] [bold white]Système[/bold white] [dim]❯[/dim] [cyan]{cmd}[/cyan]")
                else:
                    display.console.print(token, end="", markup=False, highlight=False, soft_wrap=True)
                    tokens_printed = True
                
            elif etype == "file_diff":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                _separate_from_tokens()
                filename = event.get("filename", "unknown")
                diff_lines = event.get("diff", [])
                display.code_diff(filename, diff_lines)
                
            elif etype == "sudo_request":
                if live_spinner:
                    live_spinner.stop()
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
                    live_spinner.stop()
                    live_spinner = None
                _separate_from_tokens()
                root = Path(workspace or os.getcwd()).resolve()
                out_path = receive_file(event, client, root)
                display.success(f"Fichier reçu et vérifié : {out_path}")

            elif etype == "remote_command":
                cmd = event.get("command", "")
                cwd = event.get("cwd", "")
                display.console.print(f"\n[bold yellow]⚡ Exécution locale (Mac):[/bold yellow] [cyan]{cmd}[/cyan]")
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
                display.console.print(f"\n[bold yellow]📖 Lecture locale (Mac):[/bold yellow] [dim]{path}[/dim]")
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
                display.console.print(f"\n[bold yellow]💾 Écriture locale (Mac):[/bold yellow] [dim]{path}[/dim]")
                try:
                    root = Path(workspace or os.getcwd()).resolve()
                    safe_path = _safe_target(root, path, for_write=True)
                    if safe_path is None:
                        out = f"Chemin local refusé (hors espace de travail) : {path}"
                    else:
                        with open(safe_path, "w", encoding="utf-8") as f:
                            f.write(content)
                        out = "Fichier écrit avec succès sur le Mac."
                except Exception as e:
                    out = f"Erreur d'écriture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_write_result", "value": out})

            elif etype == "reconnecting":
                display.info(f"Connexion interrompue, reprise du flux (tentative {event['attempt']}/3)...")

            elif etype == "error":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                display.error(event.get("message") or event.get("error", "Une erreur est survenue."))
                return False
                
            elif etype == "mission_complete":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                _separate_from_tokens()
                display.mission_summary(event)
                return not event.get("stopped", False)

        display.error("Flux fermé sans confirmation de fin de mission.")
        return False
                
    except KeyboardInterrupt:
        display.console.print("\n")
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
            live_spinner.stop()


def handle_temporary_agents(client: AuroraClient, agents: list[dict]) -> None:
    """Prompt user to save or delete temporary agents created during the mission."""
    display.console.print(f"\n[bold]Agents temporaires créés : {len(agents)}[/bold]")
    for a in agents:
        display.console.print(f"  • {a.get('name')} [dim]({a.get('role', 'Agent')})[/dim]")
    
    display.console.print("\n  [S] Tout sauvegarder")
    display.console.print("  [s] Tout supprimer")
    display.console.print("  [c] Choisir individuellement")
    
    choice = input("\nChoix (S/s/c) [s]: ").strip()
    
    if choice == 'S':
        for a in agents:
            client.agent_dynamic_save(a['id'])
        display.success(f"{len(agents)} agents sauvegardés de façon permanente.")
    elif choice == 'c':
        for a in agents:
            keep = input(f"Sauvegarder '{a.get('name')}' ? (o/N): ").strip().lower()
            if keep == 'o':
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
