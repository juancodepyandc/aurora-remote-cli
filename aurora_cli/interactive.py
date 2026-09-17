"""Interactive REPL mode for Aurora CLI."""
from __future__ import annotations
import signal
import sys
import time
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.markdown import Markdown

from aurora_cli import config
from aurora_cli.client import AuroraClient
from aurora_cli import display
try:
    from aurora_cli.core.jobia import JOBIACore
    jobia_engine = JOBIACore()
except ImportError:
    jobia_engine = None

try:
    from aurora_cli.agi.cognitive_loop import CognitiveEngine
    agi_engine = CognitiveEngine()
except ImportError:
    agi_engine = None

console = Console()

INTERNAL_COMMANDS = {
    "/help": "Show available commands",
    "/status": "Show server status",
    "/permissions": "Show/set permission levels",
    "/agents": "Show all agents",
    "/tools": "Show available tools",
    "/models": "Show available models",
    "/mcp": "Show MCP servers and tools",
    "/skills": "Show loaded skills",
    "/connections": "Show service connections",
    "/files": "Show files changed in current session",
    "/sources": "Show web sources consulted",
    "/session": "Show current session info",
    "/sessions": "List all sessions",
    "/clear": "Clear the terminal",
    "/stop": "Stop current task",
    "/exit": "Exit Aurora",
    "/mode": "Changer le mode (autonome, fast, base)",
}

COMMAND_COMPLETER = WordCompleter(list(INTERNAL_COMMANDS.keys()), sentence=True)


def run_interactive(client: AuroraClient) -> None:
    """Main interactive REPL loop."""
    # Show banner
    try:
        status = client.status()
        display.banner(status)
    except Exception as e:
        display.error(f"Cannot connect to Aurora server: {e}")
        console.print("[dim]Run 'jobia connect' to configure the server connection.[/dim]")
        return

    # Create session
    try:
        session_data = client.session_create(
            permissions=config.get("default_permissions", "AUTONOMOUS")
        )
        session_id = session_data.get("session", {}).get("id", "")
    except Exception:
        session_id = ""

    messages: list[dict] = []
    history_file = str(config.HISTORY_FILE)
    config.ensure_dirs()
    from prompt_toolkit.styles import Style
    from prompt_toolkit.lexers import PygmentsLexer
    from pygments.lexer import RegexLexer, bygroups
    from pygments.token import Keyword, String, Text
    
    class AuroraLexer(RegexLexer):
        tokens = {
            'root': [
                (r'(^/\w+)(\s+)(.*)$', bygroups(Keyword, Text, String)),
                (r'(^/\w+)$', Keyword),
                (r'.', Text),
            ]
        }
    
    # Style cyberpunk / moderne pour le menu déroulant (autocomplétion)
    custom_style = Style.from_dict({
        'completion-menu': 'bg:#1e1e1e #00ffff',
        'completion-menu.completion.current': 'bg:#00ffff #000000 bold',
        'completion-menu.completion': 'bg:#1e1e1e #00aaaa',
        'scrollbar.background': 'bg:#222222',
        'scrollbar.button': 'bg:#00ffff',
        'prompt': '#00ffff bold',
        'keyword': '#ff00ff bold', # Magenta pour /commande
        'string': '#ffff00',       # Jaune pour les arguments
    })

    prompt_session: PromptSession = PromptSession(
        history=FileHistory(history_file),
        completer=COMMAND_COMPLETER,
        style=custom_style,
        lexer=PygmentsLexer(AuroraLexer),
        complete_while_typing=True
    )

    # Handle Ctrl+C gracefully
    current_mission_id: str = ""

    def signal_handler(sig: int, frame: Any) -> None:
        nonlocal current_mission_id
        if current_mission_id:
            try:
                client.mission_stop(current_mission_id)
                console.print("\n[yellow]Mission arrêtée.[/yellow]")
            except Exception:
                pass
            current_mission_id = ""
        else:
            console.print("\n[dim]Ctrl+C pour interrompre, /exit pour quitter.[/dim]")

    signal.signal(signal.SIGINT, signal_handler)

    console.print("[dim]Tapez votre message ou /help pour les commandes.[/dim]\n")

    while True:
        try:
            user_input = prompt_session.prompt("J.O.B.I.A ⚡ > ", ).strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Au revoir.[/dim]")
            break

        if not user_input:
            continue

        # Internal commands
        if user_input.startswith("/"):
            cmd = user_input.split()[0].lower()
            if cmd == "/exit":
                console.print("[dim]Au revoir.[/dim]")
                break
            elif cmd == "/help":
                _show_help()
            elif cmd == "/status":
                _cmd_status(client)
            elif cmd == "/permissions":
                _cmd_permissions(client, user_input)
            elif cmd == "/agents":
                _cmd_agents(client)
            elif cmd == "/tools":
                _cmd_tools(client)
            elif cmd == "/models":
                _cmd_models(client)
            elif cmd == "/mcp":
                _cmd_mcp(client)
            elif cmd == "/skills":
                _cmd_skills(client)
            elif cmd == "/connections":
                _cmd_connections(client)
            elif cmd == "/session":
                console.print(f"[dim]Session: {session_id}[/dim]")
            elif cmd == "/sessions":
                _cmd_sessions(client)
            elif cmd == "/clear":
                console.clear()
            elif cmd == "/mode":
                if jobia_engine:
                    from rich.table import Table
                    from rich.prompt import IntPrompt
                    
                    table = Table(title="[bold magenta]✧ Configuration du Routage Neuronal ✧[/bold magenta]", border_style="cyan", show_header=True, header_style="bold cyan", expand=True)
                    table.add_column("ID", justify="center", style="bold yellow", width=4)
                    table.add_column("Mode d'Exécution", style="bold white")
                    table.add_column("Description", style="dim")
                    
                    table.add_row("1", "Autonome", "Routage dynamique et intelligent (Recommandé)")
                    table.add_row("2", "Surmultiplié", "Cloud Dédié (Performances maximales, sous quotas)")
                    table.add_row("3", "Local Strict", "Exécution 100% Locale (Aucune fuite de données)")
                    
                    console.print(table)
                    
                    choice = IntPrompt.ask("
[bold cyan]Sélectionnez un ID de mode[/bold cyan]", choices=["1", "2", "3"], show_choices=False)
                    
                    mode_map = {"1": "autonome", "2": "fast", "3": "base"}
                    selected_mode = mode_map[str(choice)]
                    
                    with console.status("[bold magenta]Reconfiguration de l'architecture en cours...[/bold magenta]", spinner="dots12"):
                        import time
                        time.sleep(1) # Simulation de la reconnexion réseau
                        if jobia_engine.set_mode(selected_mode):
                            console.print(f"[bold green]✔ Architecture verrouillée sur le mode : {selected_mode.upper()}[/bold green]")
                        else:
                            console.print("[red]✖ Rejeté (Quotas Cloud potentiellement épuisés).[/red]")
                else:
                    console.print("[red]Moteur J.O.B.I.A. non disponible.[/red]")
            elif cmd == "/stop":
                if current_mission_id:
                    client.mission_stop(current_mission_id)
                    current_mission_id = ""
                    display.success("Mission arrêtée.")
                else:
                    console.print("[dim]Aucune mission en cours.[/dim]")
            else:
                console.print(f"[dim]Commande inconnue: {cmd}. Tapez /help.[/dim]")
            continue

        # --- J.O.B.I.A. Core Execution ---
        try:
            if jobia_engine:
                def on_jobia_done(task_id, result):
                    from rich.markdown import Markdown
                    from rich.panel import Panel
                    from rich.live import Live
                    from rich.console import Group
                    import time
                    
                    console.print(f"
[bold green]✔ Tâche {task_id} traitée.[/bold green]")
                    
                    # Simulation d'affichage progressif (Typewriter Effect)
                    # Pour éviter de bloquer l'UI trop longtemps, on l'affiche par blocs.
                    lines = result.split('
')
                    displayed_text = ""
                    
                    with Live(auto_refresh=False, console=console) as live:
                        for line in lines:
                            displayed_text += line + "
"
                            md = Markdown(displayed_text)
                            panel = Panel(md, border_style="cyan", title="[bold magenta]Synthèse J.O.B.I.A[/bold magenta]", expand=False)
                            live.update(panel, refresh=True)
                            time.sleep(0.05)  # Effet d'écriture rapide
                    
                    console.print("J.O.B.I.A ⚡ > ", end="", flush=True)
                    
                console.print("[bold magenta]🧠 J.O.B.I.A analyse votre requête...[/bold magenta]")
                task_id, route, past_ctx = jobia_engine.process_request(user_input, callback=on_jobia_done)
                console.print(f"[bold cyan]Routage AGI actif :[/bold cyan] [bold magenta]{route}[/bold magenta]")
                if past_ctx:
                    console.print(f"[dim]Mémoire locale récupérée : {len(past_ctx)} entrées contextuelles.[/dim]")
                console.print(f"[dim]Délégation de la tâche {task_id} en arrière-plan (non-bloquant)...[/dim]")
            else:
                console.print("[red]ERREUR FATALE: Moteur J.O.B.I.A. hors-service. Dépannage requis.[/red]")
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrompu par l'utilisateur.[/yellow]\n")
        except Exception as e:
            display.error(f"Erreur d'exécution: {e}")
            console.print()


def _show_help() -> None:
    console.print("\n[bold]Commandes disponibles :[/bold]")
    for cmd, desc in INTERNAL_COMMANDS.items():
        console.print(f"  [cyan]{cmd:16s}[/cyan] {desc}")
    console.print()


def _cmd_status(client: AuroraClient) -> None:
    try:
        data = client.status()
        display.status_display(data)
    except Exception as e:
        display.error(f"Erreur: {e}")


def _cmd_permissions(client, user_input: str) -> None:
    parts = user_input.split()
    if len(parts) > 1:
        level = parts[1].upper()
    else:
        try:
            data = client.permissions_get()
            display.permissions_display(data.get("levels", {}))
            console.print("[cyan]Entrez le nom de la permission (ex: AUTONOMOUS, SAFE) ou laissez vide pour annuler :[/cyan]")
            level = input("Nouvelle permission > ").strip().upper()
            if not level:
                return
        except Exception as e:
            display.error(str(e))
            return
            
    try:
        result = client.permissions_set(level)
        if result.get("ok"):
            display.success(f"Permissions: {level}")
        else:
            display.error(result.get("error", "Failed"))
    except Exception as e:
        display.error(str(e))


def _cmd_agents(client: AuroraClient) -> None:
    try:
        off = client.agents_official()
        dyn = client.agents_dynamic()
        display.agents_table(off.get("agents", []), dyn.get("agents", []))
    except Exception as e:
        display.error(str(e))


def _cmd_tools(client: AuroraClient) -> None:
    try:
        data = client.tools()
        display.tools_table(data.get("tools", []))
    except Exception as e:
        display.error(str(e))


def _cmd_models(client: AuroraClient) -> None:
    try:
        data = client.models()
        display.models_table(data.get("models", []))
    except Exception as e:
        display.error(str(e))


def _cmd_mcp(client: AuroraClient) -> None:
    try:
        servers = client.mcp_list()
        tools = client.mcp_tools()
        display.mcp_table(servers.get("servers", []), tools.get("tools", []))
    except Exception as e:
        display.error(str(e))


def _cmd_skills(client: AuroraClient) -> None:
    try:
        data = client.skills_list()
        display.skills_table(data.get("skills", []))
    except Exception as e:
        display.error(str(e))


def _cmd_connections(client: AuroraClient) -> None:
    try:
        data = client.connections_list()
        display.connections_table(data.get("connections", []))
    except Exception as e:
        display.error(str(e))


def _cmd_sessions(client: AuroraClient) -> None:
    try:
        data = client.session_list()
        display.sessions_table(data.get("sessions", []))
    except Exception as e:
        display.error(str(e))
