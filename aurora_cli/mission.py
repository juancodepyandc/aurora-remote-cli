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

    display.success(f"Mission {mission_id[:8]} démarrée. Analyse de la requête en cours...")
    
    current_step = ""
    start_time = time.time()
    token_buffer = ""
    
    try:
        for event in client.mission_stream(mission_id):
            etype = event.get("type", "")
            
            if etype == "step_start":
                step_name = event.get("step", "Processing")
                current_step = step_name
                display.mission_step(step_name, event.get("index", 0), elapsed=0, done=False)
                
            elif etype == "step_end":
                elapsed = time.time() - start_time
                step_name = event.get("step", current_step)
                display.mission_step(step_name, event.get("index", 0), elapsed=elapsed, done=True)
                
            elif etype == "token":
                # For now, print tokens directly as they stream during execution
                token = event.get("content", "")
                token_buffer += token
                display.token_print(token)
                
            elif etype == "file_diff":
                filename = event.get("filename", "unknown")
                diff_lines = event.get("diff", [])
                if token_buffer:
                    display.console.print("\n")
                    token_buffer = ""
                display.code_diff(filename, diff_lines)
                
            elif etype == "sudo_request":
                reason = event.get("reason", "Action nécessite des privilèges élevés")
                if token_buffer:
                    display.console.print("\n")
                    token_buffer = ""
                pwd = display.ask_password(f"{reason}. Mot de passe sudo :")
                # Envoi du mot de passe au serveur (le serveur l'utilise puis l'oublie)
                try:
                    client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "password", "value": pwd})
                    display.success("Mot de passe transmis et supprimé localement.")
                except Exception as e:
                    display.error(f"Erreur d'envoi du mot de passe : {e}")

            elif etype == "error":
                display.error(event.get("message", "Une erreur est survenue."))
                
            elif etype == "mission_complete":
                if token_buffer:
                    display.console.print("\n") # Ensure newline after stream
                display.mission_summary(event)
                
                # Check for temporary agents
                try:
                    dyn_agents = client.agents_dynamic().get("agents", [])
                    mission_agents = [a for a in dyn_agents if a.get("created_by") == mission_id and a.get("type") == "temporary"]
                    if mission_agents:
                        handle_temporary_agents(client, mission_agents)
                except Exception:
                    pass
                return

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
