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
    "/mode": "Niveau d'effort cognitif et modèle (pro, balanced, deep, cyber, fast)",
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

    # Create or Resume Session
    try:
        from rich.prompt import Prompt
        data = client.session_list()
        sessions = data.get("sessions", [])
        
        session_id = ""
        if sessions:
            console.print("\n[bold cyan]Sessions Actives Détectées :[/bold cyan]")
            for i, s in enumerate(sessions):
                console.print(f"  [bold yellow]{i+1}.[/bold yellow] {s['id']} (Perms: {s.get('permissions', 'N/A')})")
            
            console.print("\n[dim]Tapez le numéro pour reprendre, ou Entrée pour une nouvelle session.[/dim]")
            ans = Prompt.ask("Choix", default="")
            if ans.isdigit() and 1 <= int(ans) <= len(sessions):
                session_id = sessions[int(ans)-1]["id"]
                console.print(f"[bold green]✔ Contexte restauré : {session_id}[/bold green]\n")
            
        if not session_id:
            session_data = client.session_create(permissions=config.get("default_permissions", "AUTONOMOUS"))
            session_id = session_data.get("session", {}).get("id", "")
            if sessions:
                console.print(f"[bold magenta]✨ Nouvelle session initiée : {session_id}[/bold magenta]\n")
    except Exception as e:
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
                result = client.mission_stop(current_mission_id)
                if not result.get("ok"):
                    display.error(result.get("error", "Arrêt non confirmé."))
                    return
                console.print("\n[yellow]Arrêt demandé au serveur.[/yellow]")
            except Exception:
                pass
            current_mission_id = ""
        else:
            console.print("\n[dim]Ctrl+C pour interrompre, /exit pour quitter.[/dim]")

    signal.signal(signal.SIGINT, signal_handler)

    console.print("[dim]Tapez votre message ou /help pour les commandes.[/dim]\n")

    while True:
        try:
            user_input = prompt_session.prompt("NEXUS > ", ).strip()
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
                session_id = _cmd_sessions(client, session_id)
            elif cmd == "/fresh":
                try:
                    session_data = client.session_create(permissions=config.get("default_permissions", "AUTONOMOUS"))
                    session_id = session_data.get("session", {}).get("id", "")
                    console.print(f"\n[bold green]✔ Contexte réinitialisé. Nouvelle session : {session_id}[/bold green]\n")
                except Exception as e:
                    console.print(f"[red]Erreur session : {e}[/red]")
            elif cmd == "/clear":
                console.clear()
            elif cmd == "/mode":
                if jobia_engine:
                    from rich.table import Table
                    from rich.prompt import Prompt

                    modes = list(jobia_engine.MODE_TO_MODEL.keys())
                    table = Table(title="[bold magenta]✧ Niveau d'Effort Cognitif & Routage Neuronal ✧[/bold magenta]",
                                  border_style="cyan", show_header=True, header_style="bold cyan", expand=True)
                    table.add_column("ID", justify="center", style="bold yellow", width=4)
                    table.add_column("Mode / Effort", style="bold white", width=16)
                    table.add_column("Modèle IA Déployé", style="bold green", width=25)
                    for idx, mode in enumerate(modes, 1):
                        model = jobia_engine.MODE_TO_MODEL[mode] or "Suprême Serveur (défaut)"
                        table.add_row(str(idx), mode.capitalize(), model)

                    console.print(table)

                    raw = Prompt.ask("\n[bold cyan]Mode (ID, nom, ou Entrée pour conserver le courant)[/bold cyan]", default="")
                    if not raw.strip():
                        console.print(f"[yellow]Mode conservé : {jobia_engine.mode}[/yellow]")
                        continue
                    if raw.strip().isdigit():
                        idx = int(raw.strip())
                        selected_mode = modes[idx - 1] if 1 <= idx <= len(modes) else ""
                    else:
                        selected_mode = raw.strip().lower()
                    if jobia_engine.set_mode(selected_mode):
                        model_name = jobia_engine.get_model() or "Suprême Serveur (défaut)"
                        console.print(f"[bold green]✔ Architecture verrouillée sur le mode : {selected_mode.upper()} [{model_name}][/bold green]")
                    else:
                        console.print("[red]✖ Mode non reconnu.[/red]")
                else:
                    console.print("[red]Moteur J.O.B.I.A. non disponible.[/red]")
            elif cmd == "/stop":
                if current_mission_id:
                    result = client.mission_stop(current_mission_id)
                    if result.get("ok"):
                        current_mission_id = ""
                        display.info("Arrêt demandé au serveur.")
                    else:
                        display.error(result.get("error", "Arrêt non confirmé."))
                else:
                    console.print("[dim]Aucune mission en cours.[/dim]")
            else:
                console.print(f"[dim]Commande inconnue: {cmd}. Tapez /help.[/dim]")
            continue

        # --- J.O.B.I.A. Core Execution ---
        try:
            if jobia_engine:
                import threading
                import queue
                import time
                from rich.live import Live
                from rich.panel import Panel
                from rich.markdown import Markdown
                from rich.text import Text
                from rich.spinner import Spinner
                from rich.table import Table
                from rich import box
                
                done_event = threading.Event()
                first_token_received = threading.Event()
                token_queue: queue.Queue = queue.Queue()
                task_result = {"text": "", "status": "error", "reconnecting": False, "transferred_files": []}
                
                def on_jobia_done(task_id, result):
                    nonlocal current_mission_id
                    if isinstance(result, dict) and result.get("mission_id"):
                        current_mission_id = result["mission_id"]
                    if isinstance(result, dict) and result.get("reconnecting"):
                        task_result["reconnecting"] = True
                    elif isinstance(result, dict) and result.get("file_transfer"):
                        task_result.setdefault("transferred_files", []).append(result)
                    elif isinstance(result, dict) and result.get("stream"):
                        task_result["reconnecting"] = False
                        chunk = str(result["data"])
                        token_queue.put(chunk)
                        first_token_received.set()
                    elif isinstance(result, dict) and result.get("stream_done"):
                        task_result["status"] = result.get("status", "error")
                        task_result["text"] = str(result["data"])
                        done_event.set()
                    elif isinstance(result, dict) and "data" in result:
                        task_result["status"] = result.get("status", "error")
                        if isinstance(result["data"], dict) and "data" in result["data"]:
                            task_result["text"] = str(result["data"]["data"])
                        else:
                            task_result["text"] = str(result["data"])
                        done_event.set()
                    else:
                        task_result["text"] = str(result)
                        done_event.set()
                    if done_event.is_set():
                        current_mission_id = ""
                
                console.print(f"\n[bold cyan]Routage AGI :[/bold cyan] [bold magenta]Transmission de la mission...[/bold magenta]")
                task_id, route, past_ctx = jobia_engine.process_request(user_input, callback=on_jobia_done, client=client, session_id=session_id)
                
                start_time = time.time()
                
                # Phase 1: Attente / Réflexion (Spinner compact qui ne déborde jamais le terminal)
                with Live(console=console, auto_refresh=True, refresh_per_second=10) as live:
                    while not done_event.is_set() and not first_token_received.is_set():
                        elapsed = time.time() - start_time
                        grid = Table.grid(expand=True)
                        grid.add_column()
                        grid.add_row(Spinner("dots", text=Text(" Les agents réfléchissent (Exploration de l'arbre des possibles)...", style="bold cyan")))
                        grid.add_row(f"[dim magenta]Phase de réflexion | Temps écoulé: {elapsed:.1f}s[/dim magenta]")
                        if task_result["reconnecting"]:
                            grid.add_row("[bold yellow]⚡ Reconnexion au flux distant en cours...[/bold yellow]")
                        panel = Panel(grid, border_style="magenta", title="[bold cyan]🧠 J.O.B.I.A COGNITIVE ENGINE[/bold cyan]", box=box.ROUNDED, padding=(0, 1))
                        live.update(panel)
                        time.sleep(0.06)

                # Phase 2: Flux direct de la réponse dans le terminal (aucun scroll glitch, zéro duplication)
                if first_token_received.is_set():
                    console.print(f"\n[bold cyan]┏━━━━ 🧠 Synthèse J.O.B.I.A ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓[/bold cyan]\n")
                    while not done_event.is_set() or not token_queue.empty():
                        try:
                            chunk = token_queue.get(timeout=0.05)
                            sys.stdout.write(chunk)
                            sys.stdout.flush()
                        except queue.Empty:
                            pass
                    console.print(f"\n\n[bold cyan]┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛[/bold cyan]")
                elif task_result["text"]:
                    md_final = Markdown(task_result["text"], justify="left", code_theme="monokai")
                    panel_final = Panel(md_final, border_style="cyan", title="🧠 Synthèse J.O.B.I.A", box=box.ROUNDED, padding=(1, 2))
                    console.print(panel_final)

                transferred = task_result.get("transferred_files", [])
                if transferred:
                    from rich.table import Table
                    file_table = Table(title="[bold green]📦 Fichiers transférés sur votre machine[/bold green]", border_style="green", box=box.ROUNDED)
                    file_table.add_column("Fichier", style="bold white")
                    file_table.add_column("Taille", justify="right", style="cyan")
                    file_table.add_column("Emplacement Local", style="dim green")
                    for tf in transferred:
                        size_kb = tf.get("size", 0) / 1024
                        size_str = f"{size_kb:.1f} Ko" if size_kb < 1024 else f"{size_kb/1024:.2f} Mo"
                        file_table.add_row(tf.get("filename", ""), size_str, tf.get("path", ""))
                    console.print(file_table)

                outcome = task_result["status"]
                color = "green" if outcome == "success" else "yellow" if outcome == "stopped" else "red"
                label = "✔ Mission accomplie" if outcome == "success" else "⚠ Mission arrêtée" if outcome == "stopped" else "✖ Mission interrompue ou échouée"
                console.print(f"[bold {color}]{label} en {time.time() - start_time:.1f}s.[/bold {color}]\n")

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
    known = config.PERMISSION_LEVELS
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

    if level not in known:
        display.error(f"Niveau inconnu : {level}. Niveaux autorisés : {', '.join(known)}")
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


def _cmd_sessions(client: AuroraClient, current_session_id: str = "") -> str:
    try:
        data = client.session_list()
        sessions = data.get("sessions", [])
        if not sessions:
            console.print("[yellow]Aucune session active.[/yellow]")
            return current_session_id
            
        import questionary
        choices = [{"name": f"Session {s['id']} (Msg: {s.get('message_count',0)})", "value": s["id"]} for s in sessions]
        action = questionary.select(
            "Gérer les sessions :",
            choices=[
                {"name": "Lister les sessions", "value": "list"},
                {"name": "Reprendre une session", "value": "resume"},
                {"name": "Supprimer une session", "value": "delete"},
                {"name": "Annuler", "value": "cancel"}
            ]
        ).ask()
        
        if action == "list":
            display.sessions_table(sessions)
        elif action == "resume":
            sid = questionary.select("Choisir la session à reprendre:", choices=choices).ask()
            if sid:
                console.print(f"[bold green]✔ Session reprise : {sid}[/bold green]")
                return sid
        elif action == "delete":
            sid = questionary.select("Choisir la session à supprimer:", choices=choices).ask()
            if sid:
                res = client.session_delete(sid)
                if res.get("ok"):
                    console.print(f"[bold red]Session {sid} supprimée avec succès.[/bold red]")
                    if sid == current_session_id:
                        console.print("[yellow]Vous avez supprimé la session actuelle. Tapez /fresh pour en recréer une.[/yellow]")
                else:
                    console.print(f"[red]Erreur: {res.get('error')}[/red]")
    except Exception as e:
        display.error(str(e))
    return current_session_id
