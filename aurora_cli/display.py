"""Rich terminal display for Aurora CLI."""
from __future__ import annotations
from typing import Any
import time

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner
from rich.columns import Columns
from rich.align import Align
from rich import box
from rich.prompt import Prompt

console = Console()

def banner(status_data: dict) -> None:
    """Affiche une bannière futuriste et animée."""
    frames = [
        "[bold magenta]A[/bold magenta]",
        "[bold magenta]AU[/bold magenta]",
        "[bold cyan]AUR[/bold cyan]",
        "[bold cyan]AURO[/bold cyan]",
        "[bold blue]AUROR[/bold blue]",
        "[bold blue]AURORA[/bold blue]",
        "[bold white]A U R O R A[/bold white]",
        "[bold white]A U R O R A[/bold white] [dim cyan]S Y N C H R O N I Z I N G . . .[/dim cyan]",
    ]
    
    with Live(auto_refresh=False, transient=True) as live:
        for frame in frames:
            content = f"{frame}\n\n[dim]Établissement de la connexion montante...[/dim]"
            live.update(Panel(Align.center(content), border_style="cyan", width=65, box=box.ROUNDED))
            live.refresh()
            time.sleep(0.06)
        time.sleep(0.2)
        
    hw = status_data.get("hardware", {})
    gpu_info = hw.get("gpu", "N/A")
    vram = hw.get("vram_total_gb", 0)
    if vram:
        gpu_info += f" ({vram} GB)"
        
    final_content = (
        "[bold white tracking=2]A U R O R A   N E X U S[/bold white tracking=2]\n"
        "[dim cyan]─────────────────────────────────────────[/dim cyan]\n"
        f"[dim]Connexion[/dim]    : [bold green]Active[/bold green]\n"
        f"[dim]Moteur IA[/dim]    : [bold cyan]{gpu_info}[/bold cyan]\n"
        f"[dim]Cognition[/dim]    : [bold magenta]Pleine capacité[/bold magenta]\n"
        "[dim cyan]─────────────────────────────────────────[/dim cyan]"
    )
    console.print(Panel(Align.center(final_content), border_style="bold blue", width=65, box=box.DOUBLE_EDGE))
    console.print()

def system_status(status: dict) -> None:
    """Display system status details."""
    hw = status.get("hardware", {})
    
    table = Table(title="Diagnostic Système", show_header=False, box=box.ROUNDED, title_style="bold cyan")
    table.add_column("Composant", style="bold blue")
    table.add_column("Statut", style="green")
    table.add_column("Détail", style="dim")
    
    table.add_row("Serveur", "En ligne", "Aurora Bridge Server")
    table.add_row("GPU", hw.get("gpu", "N/A"), f"{hw.get('vram_total_gb', 0)} GB VRAM")
    table.add_row("RAM", f"{hw.get('ram_gb', 0)} GB", hw.get("os", ""))
    
    console.print(table)
    console.print()

def models_table(models: list[dict]) -> None:
    """Display available models."""
    table = Table(title="[bold]Modèles d'Intelligence[/bold]", show_header=True, box=box.MINIMAL_DOUBLE_HEAD, header_style="bold magenta")
    table.add_column("Nom")
    table.add_column("Famille")
    table.add_column("Paramètres", justify="right")
    
    for m in models:
        table.add_row(f"[cyan]{m.get('name', '')}[/cyan]", m.get("family", ""), m.get("parameters", ""))
    console.print(table)
    console.print()

def tools_table(tools: list[dict]) -> None:
    """Display available tools."""
    table = Table(title="[bold]Arsenal Outils[/bold]", show_header=True, box=box.SIMPLE_HEAD, header_style="bold yellow")
    table.add_column("Outil", style="bold cyan")
    table.add_column("Description")
    table.add_column("Source", style="dim", justify="right")
    
    for t in tools:
        source = t.get("source", "built-in") if t.get("source") else "built-in"
        table.add_row(t.get("name", ""), t.get("desc", t.get("description", "")), source)
    console.print(table)
    console.print()

def agents_table(official: list[dict], dynamic: list[dict]) -> None:
    """Display agents (official + dynamic)."""
    console.print(Panel("[bold]Structure des Agents[/bold]", style="bold blue", box=box.SQUARE, expand=False))
    
    console.print(f" [cyan]●[/cyan] [bold white]Experts Officiels[/bold white] [dim]({len(official)})[/dim]")
    for a in official:
        icon = "[bold green]✓[/bold green]" if a.get("enabled", True) else "[bold red]✗[/bold red]"
        console.print(f"    {icon} [bold]{a['name']}[/bold] — [dim]{a.get('description', '')}[/dim]")

    if dynamic:
        console.print(f"\n [magenta]◆[/magenta] [bold white]Experts Dynamiques[/bold white] [dim]({len(dynamic)})[/dim]")
        for a in dynamic:
            console.print(f"    [magenta]◆[/magenta] [bold]{a['name']}[/bold] — [dim]{a.get('role', '')}[/dim]")
    console.print()

def sessions_table(sessions: list[dict]) -> None:
    """Display session list."""
    if not sessions:
        console.print("[dim italic]Aucune session active mémorisée.[/dim italic]\n")
        return
        
    table = Table(title="[bold]Sessions d'Engagement[/bold]", show_header=True, box=box.ROUNDED, header_style="bold blue")
    table.add_column("ID Session", style="bold cyan")
    table.add_column("Création", style="dim")
    table.add_column("Interactions", justify="right")
    table.add_column("Niveau Permission", style="bold magenta")
    
    for s in sessions:
        table.add_row(s["id"], s.get("created_at", "")[:19], str(s.get("message_count", 0)), s.get("permissions", ""))
    console.print(table)
    console.print()

def permissions_display(levels: dict, current: str = "") -> None:
    """Display permission levels."""
    table = Table(title="[bold]Niveaux d'Accès[/bold]", show_header=True, box=box.MINIMAL, header_style="bold yellow")
    table.add_column("Niveau", style="bold")
    table.add_column("Capacités Autorisées")
    
    for name, perms in levels.items():
        active = [k for k, v in perms.items() if v]
        is_current = (name == current)
        style = "bold green" if is_current else "white"
        marker = " ◀ [bold green]ACTIF[/bold green]" if is_current else ""
        caps = ", ".join(active[:6]) + ("..." if len(active) > 6 else "")
        table.add_row(f"[{style}]{name}[/{style}]{marker}", f"[dim]{caps}[/dim]")
        
    console.print(table)
    console.print()

def skills_table(skills: list[dict]) -> None:
    """Display skills."""
    table = Table(title="[bold]Compétences (Skills)[/bold]", show_header=True, box=box.ROUNDED, header_style="bold cyan")
    table.add_column("Nom", style="bold blue")
    table.add_column("Niveau")
    table.add_column("Description")
    table.add_column("Triggers", style="dim")
    
    for s in skills:
        table.add_row(s.get("name", ""), s.get("level", ""), s.get("description", ""), ", ".join(s.get("triggers", [])[:3]))
    console.print(table)
    console.print()

def mcp_table(servers: list[dict], tools: list[dict] | None = None) -> None:
    """Display MCP servers and tools."""
    table = Table(title="[bold]Serveurs MCP[/bold]", show_header=True, box=box.SIMPLE)
    table.add_column("Serveur", style="bold magenta")
    table.add_column("Commande")
    table.add_column("Source", style="dim")
    
    for s in servers:
        table.add_row(s.get("name", ""), s.get("command", ""), s.get("source", ""))
    console.print(table)
    console.print()

    if tools:
        t_table = Table(title="[bold]Outils MCP[/bold]", show_header=True, box=box.SIMPLE)
        t_table.add_column("Serveur", style="dim")
        t_table.add_column("Outil", style="bold cyan")
        t_table.add_column("Description")
        for t in tools:
            t_table.add_row(t.get("server", ""), t.get("name", ""), t.get("description", ""))
        console.print(t_table)
        console.print()

def connections_table(connections: list[dict]) -> None:
    """Display service connections."""
    table = Table(title="[bold]Services Connectés[/bold]", show_header=True, box=box.ROUNDED)
    table.add_column("Service", style="bold cyan")
    table.add_column("Statut")
    table.add_column("Capacités", style="dim")
    
    for c in connections:
        icon = "[bold green]En Ligne[/bold green]" if c.get("active") else "[bold red]Hors Ligne[/bold red]"
        table.add_row(c.get("name", c.get("service", "")), icon, ", ".join(c.get("capabilities", [])))
    console.print(table)
    console.print()

def mission_step(step: str, index: int, elapsed: float = 0, done: bool = False) -> None:
    """Display a mission step."""
    mins = int(elapsed) // 60
    secs = int(elapsed) % 60
    time_str = f"[{mins:02d}:{secs:02d}]"
    if done:
        icon = "[bold green]✔[/bold green]"
        style = "bold white"
    else:
        icon = "[bold yellow]◎[/bold yellow]"
        style = "cyan"
        
    console.print(f"  [dim]{time_str}[/dim] {icon} [{style}]{step}[/{style}]")

def mission_summary(data: dict) -> None:
    """Display mission completion summary."""
    total = data.get("total_seconds", 0)
    mins = int(total) // 60
    secs = int(total) % 60
    
    content = f"[bold green]✔ Mission Accomplie avec Succès[/bold green]\n\n"
    content += f"⏱️ Temps total : [bold]{mins:02d}:{secs:02d}[/bold]\n"
    
    files = data.get("files_changed", [])
    sources = data.get("sources_consulted", [])
    errors = data.get("errors_count", 0)
    
    if files:
        content += f"📄 Fichiers modifiés : [bold cyan]{len(files)}[/bold cyan]\n"
    if sources:
        content += f"🌐 Sources consultées : [bold blue]{len(sources)}[/bold blue]\n"
    if errors:
        content += f"⚠️ Erreurs gérées : [bold red]{errors}[/bold red]\n"
        
    console.print(Panel(content.strip(), border_style="green", expand=False, box=box.ROUNDED))
    console.print()

def web_activity(action: str, url: str = "", query: str = "") -> None:
    """Display web activity."""
    if query:
        console.print(f"  [cyan]🔍 Recherche Web[/cyan] : [italic]{query}[/italic]")
    if url:
        console.print(f"  [blue]🔗 {action}[/blue] : [link={url}][dim]{url}[/dim][/link]")

def error(msg: str) -> None:
    console.print(f"\n[bold red]✖ ERREUR:[/bold red] {msg}\n")

def success(msg: str) -> None:
    console.print(f"\n[bold green]✔ SUCCÈS:[/bold green] {msg}\n")

def info(msg: str) -> None:
    console.print(f"[dim italic]ℹ {msg}[/dim italic]")

def token_print(text: str) -> None:
    """Print a token inline (for streaming)."""
    console.print(f"[white]{text}[/white]", end="", highlight=False)

def code_diff(filename: str, diff_lines: list[str]) -> None:
    """Affiche de manière stylisée un diff de code (ajout/suppression)."""
    table = Table(show_header=False, box=box.SIMPLE_HEAD, padding=(0, 1), collapse_padding=True, expand=True)
    table.add_column("Ligne", justify="right", style="dim", width=5)
    table.add_column("Modification")
    
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
            table.add_row("...", f"[bold cyan]{line_clean}[/bold cyan]")
        else:
            table.add_row(str(line_num), f"[dim]{line_clean}[/dim]")
            line_num += 1
            
    panel = Panel(table, title=f"📝 Modification : [bold]{filename}[/bold]", border_style="magenta", box=box.ROUNDED)
    console.print(panel)
    console.print()

def ask_password(prompt: str) -> str:
    """Demande un mot de passe à l'utilisateur de manière sécurisée (masqué)."""
    console.print(f"\n[bold yellow]🔒 Autorisation Système Requise[/bold yellow]")
    return Prompt.ask(f"[bold cyan]{prompt}[/bold cyan]", password=True)
