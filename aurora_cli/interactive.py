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
        console.print("[dim]Run 'aurora connect' to configure the server connection.[/dim]")
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
    prompt_session: PromptSession = PromptSession(
        history=FileHistory(history_file),
        completer=COMMAND_COMPLETER,
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
            user_input = prompt_session.prompt("Aurora > ", ).strip()
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

        # Chat message
        messages.append({"role": "user", "content": user_input})
        console.print()

        try:
            full_response = ""
            for event in client.chat_stream(messages, session_id=session_id):
                etype = event.get("type", "")
                if etype == "token":
                    token = event.get("content", "")
                    display.token_print(token)
                    full_response += token
                elif etype == "done":
                    pass
                elif etype == "error":
                    display.error(event.get("error", "Unknown error"))
            console.print("\n")
            if full_response:
                messages.append({"role": "assistant", "content": full_response})
        except KeyboardInterrupt:
            console.print("\n[yellow]Interrompu.[/yellow]\n")
        except Exception as e:
            display.error(f"Erreur: {e}")
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


def _cmd_permissions(client: AuroraClient, user_input: str) -> None:
    parts = user_input.split()
    if len(parts) > 1:
        level = parts[1].upper()
        try:
            result = client.permissions_set(level)
            if result.get("ok"):
                display.success(f"Permissions: {level}")
            else:
                display.error(result.get("error", "Failed"))
        except Exception as e:
            display.error(str(e))
    else:
        try:
            data = client.permissions_get()
            display.permissions_display(data.get("levels", {}))
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
