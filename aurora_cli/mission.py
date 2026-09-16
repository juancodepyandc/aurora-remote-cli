"""Mission tracking and display for Aurora CLI."""
from __future__ import annotations
import json
import time

from rich.live import Live
from rich.spinner import Spinner
from rich.console import Group
from rich.text import Text

from aurora_cli import display
from aurora_cli.client import AuroraClient


def run_mission(client: AuroraClient, request: str, workspace: str = "", permissions: str = "AUTONOMOUS", model: str = "", session_id: str = "") -> None:
    """Start and monitor an autonomous mission."""
    try:
        data = client.mission_start(request, workspace=workspace, permissions=permissions, model=model, session_id=session_id)
        mission_id = data.get("mission_id")
        if not mission_id:
            display.error("Impossible de démarrer la mission.")
            return
    except Exception as e:
        display.error(f"Erreur de démarrage: {e}")
        return

    from rich.panel import Panel
    from rich.text import Text
    import os
    
    actual_ws = workspace or os.getcwd()
    is_remote = not actual_ws.startswith("/home/") and not actual_ws.startswith("/tmp/")
    
    info_text = Text()
    info_text.append("🚀 Mission : ", style="bold green")
    
    # Clean up request for display
    display_req = request.replace('\n', ' ')
    if len(display_req) > 55:
        display_req = display_req[:55] + "..."
        
    info_text.append(f"{display_req}\n")
    info_text.append("📂 Confinement : ", style="bold blue")
    info_text.append(f"{actual_ws}\n")
    info_text.append("🔌 Exécution : ", style="bold magenta")
    info_text.append(f"{'Locale (Mac/Win)' if is_remote else 'Distante (Linux)'} - Permissions: {permissions}\n\n")
    info_text.append("Analyse de la requête en cours...", style="dim italic")
    
    display.console.print(Panel(info_text, title="[bold cyan]✧ Aurora-IA Initialisation[/bold cyan]", border_style="cyan"))
    
    current_step = ""
    start_time = time.time()
    token_buffer = ""
    
    from rich.status import Status
    live_spinner = None

    # UI state
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.text import Text
    import re
    
    live_spinner = None
    reflection_start = 0

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
                    elapsed = event.get("elapsed", 0)
                    mins = int(elapsed) // 60
                    secs = int(elapsed) % 60
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
                    # Indent raw token output slightly for aesthetics
                    # If it has newlines, indent the next line
                    lines = token.split("\n")
                    for i, line in enumerate(lines):
                        if i == len(lines) - 1:
                            display.console.print(f"  [dim]{line}[/dim]", end="", highlight=False)
                        else:
                            display.console.print(f"  [dim]{line}[/dim]", highlight=False)
                
            elif etype == "file_diff":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                filename = event.get("filename", "unknown")
                diff_lines = event.get("diff", [])
                if token_buffer:
                    display.console.print("\n")
                    token_buffer = ""
                display.code_diff(filename, diff_lines)
                
            elif etype == "sudo_request":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                reason = event.get("reason", "Action nécessite des privilèges élevés")
                if token_buffer:
                    display.console.print("\n")
                    token_buffer = ""
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
                import base64
                from pathlib import Path
                filename = event.get("filename", "downloaded_file")
                b64data = event.get("data", "")
                
                import platform
                
                # Détection Android (Termux)
                if "com.termux" in os.environ.get("PREFIX", ""):
                    out_dir = Path("/storage/emulated/0/Download")
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / filename
                # Détection iOS (a-Shell)
                elif "APPDIR" in os.environ and "a-Shell" in os.environ["APPDIR"]:
                    out_dir = Path.home()  # a-Shell home (Documents)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / filename
                # Détection Mac/Windows/Linux (Comportement normal : dossier actuel)
                else:
                    actual_ws = workspace or os.getcwd()
                    out_path = Path(actual_ws) / filename
                if token_buffer:
                    display.console.print("\n")
                    token_buffer = ""
                try:
                    out_path.write_bytes(base64.b64decode(b64data))
                    display.success(f"Fichier reçu et enregistré sur votre Mac : {out_path}")
                except Exception as e:
                    display.error(f"Erreur lors de l'enregistrement du fichier : {e}")


                    
            elif etype == "remote_command":
                cmd = event.get("command", "")
                cwd = event.get("cwd", "")
                display.console.print(f"\n[bold yellow]⚡ Exécution locale (Mac):[/bold yellow] [cyan]{cmd}[/cyan]")
                try:
                    import subprocess
                    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=120)
                    out = result.stdout + "\n" + result.stderr
                    if not out.strip(): out = "Commande réussie sans sortie."
                except Exception as e:
                    out = f"Erreur d'exécution locale: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_command_result", "value": out})

            elif etype == "remote_read_file":
                path = event.get("path", "")
                display.console.print(f"\n[bold yellow]📖 Lecture locale (Mac):[/bold yellow] [dim]{path}[/dim]")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        out = f.read()
                except Exception as e:
                    out = f"Erreur de lecture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_read_result", "value": out})

            elif etype == "remote_write_file":
                path = event.get("path", "")
                content = event.get("content", "")
                display.console.print(f"\n[bold yellow]💾 Écriture locale (Mac):[/bold yellow] [dim]{path}[/dim]")
                try:
                    import os
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    out = "Fichier écrit avec succès sur le Mac."
                except Exception as e:
                    out = f"Erreur d'écriture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_write_result", "value": out})

            elif etype == "error":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                display.error(event.get("message", "Une erreur est survenue."))
                
            elif etype == "mission_complete":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                if token_buffer:
                    display.console.print("\n")
                    token_buffer = ""
                display.mission_summary(event)
                break
                
    except KeyboardInterrupt:
        display.console.print("\n")
        display.info("Interruption demandée, arrêt de la mission sur le serveur...")
        try:
            client.mission_stop(mission_id)
            display.success("Mission arrêtée.")
        except Exception as e:
            display.error(f"Erreur lors de l'arrêt: {e}")


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
