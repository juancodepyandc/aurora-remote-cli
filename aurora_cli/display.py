"""Rich terminal display for Aurora CLI."""
from __future__ import annotations
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner
from rich.columns import Columns

console = Console()


    
def banner(status_data: dict) -> None:
    """Affiche une bannière futuriste et animée."""
    from rich.live import Live
    from rich.panel import Panel
    from rich.align import Align
    import time
    
    frames = [
        "[bold cyan]A[/bold cyan]",
        "[bold cyan]AU[/bold cyan]",
        "[bold cyan]AUR[/bold cyan]",
        "[bold cyan]AURO[/bold cyan]",
        "[bold cyan]AUROR[/bold cyan]",
        "[bold cyan]AURORA[/bold cyan]",
        "[bold cyan]A U R O R A[/bold cyan]",
        "[bold cyan]A U R O R A[/bold cyan] [white]I N I T I A L I Z I N G . . .[/white]",
    ]
    
    with Live(auto_refresh=False) as live:
        for frame in frames:
            content = f"{frame}\n\n[dim]Système distant synchronisé.[/dim]"
            live.update(Panel(Align.center(content), border_style="cyan", width=60))
            live.refresh()
            time.sleep(0.08)
            
        time.sleep(0.3)
        
        hw = status_data.get("hardware", {})
        gpu_info = hw.get("gpu", "N/A")
        vram = hw.get("vram_total_gb", 0)
        if vram:
            gpu_info += f" ({vram} GB)"
            
        final_content = (
            "[bold white]A U R O R A   N E X U S[/bold white]\n"
            "[bold cyan]───────────────────────────────────[/bold cyan]\n"
            f"[dim]Serveur distant[/dim]  : [green]En ligne[/green]\n"
            f"[dim]Puissance Brute[/dim]  : [cyan]{gpu_info}[/cyan]\n"
            f"[dim]Mode Autonome[/dim]    : [magenta]Opérationnel[/magenta]\n"
            "[bold cyan]───────────────────────────────────[/bold cyan]"
        )
        live.update(Panel(Align.center(final_content), border_style="bold blue", width=60))
        live.refresh()
    console.print()


def doctor_results(checks: list[dict]) -> None:
    """Display aurora doctor results."""
    console.print()
    for check in checks:
        icon = "[green]✓[/green]" if check.get("ok") else "[red]✗[/red]"
        name = check.get("name", "")
        detail = check.get("detail", "")
        line = f"  {icon} {name}"
        if detail:
            line += f"  [dim]{detail}[/dim]"
        console.print(line)
    console.print()


def status_display(data: dict) -> None:
    """Display server status."""
    table = Table(title="Server Status", show_header=True, border_style="cyan")
    table.add_column("Component", style="bold")
    table.add_column("Status")
    table.add_column("Detail", style="dim")

    table.add_row("Bridge", "[green]●[/green] Active" if data.get("bridge") else "[red]●[/red] Down", "Port 3001")
    table.add_row("Ollama", "[green]●[/green] Active" if data.get("ollama") else "[red]●[/red] Down",
                  f"{data.get('models_count', 0)} models")
    table.add_row("ComfyUI", "[green]●[/green] Active" if data.get("comfyui") else "[yellow]○[/yellow] Standby", "")

    hw = data.get("hardware", {})
    table.add_row("GPU", f"[yellow]{hw.get('gpu', 'N/A')}[/yellow]",
                  f"{hw.get('vram_free_gb', 0)}/{hw.get('vram_total_gb', 0)} GB free")
    table.add_row("RAM", f"{hw.get('ram_gb', 0)} GB", hw.get("os", ""))

    console.print(table)


def models_table(models: list[dict]) -> None:
    """Display available models."""
    table = Table(title="Models", show_header=True, border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Family")
    table.add_column("Parameters")
    for m in models:
        table.add_row(m.get("name", ""), m.get("family", ""), m.get("parameters", ""))
    console.print(table)


def tools_table(tools: list[dict]) -> None:
    """Display available tools."""
    table = Table(title="Tools", show_header=True, border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Description")
    table.add_column("Source", style="dim")
    for t in tools:
        source = t.get("source", "built-in") if t.get("source") else "built-in"
        table.add_row(t.get("name", ""), t.get("desc", t.get("description", "")), source)
    console.print(table)


def agents_table(official: list[dict], dynamic: list[dict]) -> None:
    """Display agents (official + dynamic)."""
    console.print(f"\n[bold]Agents officiels ({len(official)})[/bold] — 🔒 Protégés")
    for a in official:
        icon = "[green]●[/green]" if a.get("enabled", True) else "[red]○[/red] (désactivé)"
        console.print(f"  {icon} [bold]{a['name']}[/bold]  [dim]{a.get('description', '')}[/dim]")

    if dynamic:
        console.print(f"\n[bold]Agents dynamiques ({len(dynamic)})[/bold] — Sauvegardés")
        for a in dynamic:
            console.print(f"  [magenta]◆[/magenta] [bold]{a['name']}[/bold]  [dim]{a.get('role', '')}[/dim]")
    console.print()


def sessions_table(sessions: list[dict]) -> None:
    """Display session list."""
    if not sessions:
        console.print("[dim]Aucune session.[/dim]")
        return
    table = Table(title="Sessions", show_header=True, border_style="cyan")
    table.add_column("ID", style="bold")
    table.add_column("Created")
    table.add_column("Messages")
    table.add_column("Permissions")
    for s in sessions:
        table.add_row(s["id"], s.get("created_at", "")[:19], str(s.get("message_count", 0)),
                      s.get("permissions", ""))
    console.print(table)


def permissions_display(levels: dict, current: str = "") -> None:
    """Display permission levels."""
    table = Table(title="Permission Levels", show_header=True, border_style="cyan")
    table.add_column("Level", style="bold")
    table.add_column("Capabilities")
    for name, perms in levels.items():
        active = [k for k, v in perms.items() if v]
        marker = " ← current" if name == current else ""
        table.add_row(f"{'[green]' if name == current else ''}{name}{marker}{'[/green]' if name == current else ''}",
                      ", ".join(active[:6]) + ("..." if len(active) > 6 else ""))
    console.print(table)


def skills_table(skills: list[dict]) -> None:
    """Display skills."""
    table = Table(title="Skills", show_header=True, border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Level")
    table.add_column("Description")
    table.add_column("Triggers", style="dim")
    for s in skills:
        table.add_row(s.get("name", ""), s.get("level", ""),
                      s.get("description", ""), ", ".join(s.get("triggers", [])[:3]))
    console.print(table)


def mcp_table(servers: list[dict], tools: list[dict] | None = None) -> None:
    """Display MCP servers and tools."""
    table = Table(title="MCP Servers", show_header=True, border_style="cyan")
    table.add_column("Server", style="bold")
    table.add_column("Command")
    table.add_column("Source", style="dim")
    for s in servers:
        table.add_row(s.get("name", ""), s.get("command", ""), s.get("source", ""))
    console.print(table)

    if tools:
        t_table = Table(title="MCP Tools", show_header=True, border_style="cyan")
        t_table.add_column("Server", style="dim")
        t_table.add_column("Tool", style="bold")
        t_table.add_column("Description")
        for t in tools:
            t_table.add_row(t.get("server", ""), t.get("name", ""), t.get("description", ""))
        console.print(t_table)


def connections_table(connections: list[dict]) -> None:
    """Display service connections."""
    table = Table(title="Service Connections", show_header=True, border_style="cyan")
    table.add_column("Service", style="bold")
    table.add_column("Status")
    table.add_column("Capabilities", style="dim")
    for c in connections:
        icon = "[green]●[/green]" if c.get("active") else "[red]○[/red]"
        table.add_row(c.get("name", c.get("service", "")), icon,
                      ", ".join(c.get("capabilities", [])))
    console.print(table)


def mission_step(step: str, index: int, elapsed: float = 0, done: bool = False) -> None:
    """Display a mission step."""
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    time_str = f"[{mins:02d}:{secs:02d}]"
    icon = "[green]✓[/green]" if done else "[yellow]●[/yellow]"
    console.print(f"  {time_str} {icon} {step}")


def mission_summary(data: dict) -> None:
    """Display mission completion summary."""
    total = data.get("total_seconds", 0)
    mins = int(total) // 60
    secs = int(total) % 60
    console.print(f"\n[green]✓ Mission terminée[/green]")
    console.print(f"\n  Temps total : [bold]{mins:02d}:{secs:02d}[/bold]")
    files = data.get("files_changed", [])
    sources = data.get("sources_consulted", [])
    errors = data.get("errors_count", 0)
    if files:
        console.print(f"  Fichiers   : {len(files)} modifiés")
    if sources:
        console.print(f"  Sources    : {len(sources)} consultées")
    if errors:
        console.print(f"  Erreurs    : [red]{errors}[/red]")
    console.print()


def web_activity(action: str, url: str = "", query: str = "") -> None:
    """Display web activity."""
    if query:
        console.print(f"  🌐 Recherche : [italic]{query}[/italic]")
    if url:
        console.print(f"  🌐 {action} : [link={url}]{url}[/link]")


def error(msg: str) -> None:
    console.print(f"[red]✗ {msg}[/red]")


def success(msg: str) -> None:
    console.print(f"[green]✓ {msg}[/green]")


def info(msg: str) -> None:
    console.print(f"[dim]{msg}[/dim]")


def token_print(text: str) -> None:
    """Print a token inline (for streaming)."""
    console.print(text, end="", highlight=False)

def code_diff(filename: str, diff_lines: list[str]) -> None:
    """Affiche de manière stylisée un diff de code (ajout/suppression)."""
    console.print(f"\n[bold bright_magenta]⚡ Modification de : {filename}[/bold bright_magenta]")
    table = Table(show_header=False, box=None, padding=(0, 1), collapse_padding=True)
    table.add_column("Ligne", justify="right", style="dim", width=4)
    table.add_column("Contenu")
    
    line_num = 1
    for line in diff_lines:
        line_clean = line.rstrip("\n")
        if line_clean.startswith("+"):
            table.add_row(str(line_num), f"[bold green]{line_clean}[/bold green]")
            line_num += 1
        elif line_clean.startswith("-"):
            table.add_row(str(line_num), f"[bold red]{line_clean}[/bold red]")
            line_num += 1
        elif line_clean.startswith("@@"):
            table.add_row("...", f"[cyan]{line_clean}[/cyan]")
        else:
            table.add_row(str(line_num), f"[dim]{line_clean}[/dim]")
            line_num += 1
            
    console.print(Panel(table, border_style="magenta", expand=False))
    console.print()

def ask_password(prompt: str) -> str:
    """Demande un mot de passe à l'utilisateur de manière sécurisée (masqué)."""
    from rich.prompt import Prompt
    console.print(f"\n[bold yellow]🔒 Autorisation requise[/bold yellow]")
    return Prompt.ask(f"[bold cyan]{prompt}[/bold cyan]", password=True)

