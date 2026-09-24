"""Rich terminal display for Aurora CLI (PRO DESIGN)."""
import time
import random
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from rich.align import Align
from rich import box

console = Console()

def clear():
    console.clear()

def animate_matrix_text(text_str: str, style: str = "bold green", speed: float = 0.02) -> Text:
    """Animates text like matrix decryption before settling on the real text."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789@#$%&*<>"
    for _ in range(3):
        fake = "".join(random.choice(chars) for _ in text_str)
        console.print(Text(fake, style="dim green"), end="\r")
        time.sleep(speed * 1.5)
    
    t = Text(text_str, style=style)
    console.print(t)
    return t

def banner(status_data: dict) -> None:
    """Affiche une bannière ultra-moderne futuriste et animée."""
    logo = r"""
    █████╗ ██╗   ██╗██████╗  ██████╗ ██████╗  █████╗ 
   ██╔══██╗██║   ██║██╔══██╗██╔═══██╗██╔══██╗██╔══██╗
   ███████║██║   ██║██████╔╝██║   ██║██████╔╝███████║
   ██╔══██║██║   ██║██╔══██╗██║   ██║██╔══██╗██╔══██║
   ██║  ██║╚██████╔╝██║  ██║╚██████╔╝██║  ██║██║  ██║
   ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝ ╚═════╝ ╚═╝  ╚═╝╚═╝  ╚═╝
    """
    
    with Live(auto_refresh=False, transient=True) as live:
        # Glitch effect
        for _ in range(3):
            glitch = logo.replace("██", random.choice(["▓▓", "▒▒", "░░"]))
            panel = Panel(Align.center(f"[bold cyan]{glitch}[/bold cyan]"), border_style="cyan", box=box.HEAVY)
            live.update(panel)
            live.refresh()
            time.sleep(0.1)
            
        # Clean logo animation
        for i in range(len(logo.split('\n'))):
            partial_logo = '\n'.join(logo.split('\n')[:i+1])
            panel = Panel(Align.center(f"[bold magenta]{partial_logo}[/bold magenta]"), border_style="magenta", box=box.DOUBLE)
            live.update(panel)
            live.refresh()
            time.sleep(0.08)
        time.sleep(0.3)
        
    hw = status_data.get("hardware") or {}
    gpu_info = hw.get("gpu", "N/A")
    vram = hw.get("vram_total_gb", 0)
    if vram:
        gpu_info += f" ({vram} GB)"
        
    # Build System Information Layout
    sys_table = Table(box=box.MINIMAL_DOUBLE_HEAD, expand=True, show_header=False)
    sys_table.add_column("Key", style="cyan", justify="right")
    sys_table.add_column("Value", style="green")
    sys_table.add_row("Connexion", "⚡ SYNCHRONISÉE")
    sys_table.add_row("Réseau (Tunnel)", status_data.get("tunnel_url", "Local"))
    sys_table.add_row("GPU", gpu_info)
    sys_table.add_row("CPU", f"{hw.get('cpu_cores', '?')} Cores - RAM: {hw.get('ram_total_gb', '?')} GB")

    sys_panel = Panel(
        sys_table, 
        title="[bold magenta]N E X U S   C O R E[/bold magenta]", 
        border_style="magenta",
        box=box.ROUNDED,
        padding=(1, 2)
    )
    
    console.print(sys_panel)
    console.print()

def header(title: str, subtitle: str = "") -> None:
    text = f"[bold white]{title}[/bold white]"
    if subtitle:
        text += f" | [dim cyan]{subtitle}[/dim cyan]"
    console.print(Panel(text, border_style="cyan", box=box.SQUARE))

def success(msg: str) -> None:
    console.print(f"[bold green]✔[/bold green] {msg}")

def error(msg: str) -> None:
    console.print(f"[bold red]✖[/bold red] {msg}")

def warning(msg: str) -> None:
    console.print(f"[bold yellow]⚠[/bold yellow] {msg}")

def info(msg: str) -> None:
    console.print(f"[bold cyan]ℹ[/bold cyan] {msg}")

def mission_summary(event: dict) -> None:
    """Affiche le résumé de la mission une fois terminée."""
    result = event.get("result", "")
    summary_panel = Panel(
        Text(result, style="green"),
        title="[bold green]✓ Mission Terminée[/bold green]",
        border_style="green",
        padding=(1, 2)
    )
    console.print(summary_panel)

def typing_effect(text_content: str, speed: float = 0.015, title: str = "Assistant"):
    """Un effet de machine à écrire ultra-fluide avec syntax highlighting Markdown."""
    displayed = ""
    lines = text_content.split('\n')
    
    with Live(auto_refresh=False, console=console) as live:
        for line in lines:
            for char in line:
                displayed += char
                md = Markdown(displayed, justify="left", code_theme="monokai")
                panel = Panel(md, border_style="cyan", title=f"[bold magenta]{title}[/bold magenta]", expand=False)
                live.update(panel, refresh=True)
                time.sleep(speed)
            displayed += "\n"
            time.sleep(speed * 5)


def doctor_results(checks: list[dict]) -> None:
    """Display aurora doctor results."""
    console.print()
    for check in checks:
        icon = "[green]✓[/green]" if check.get("ok") else "[red]✗[/red]"
        name = check.get("name", "?")
        detail = check.get("detail", "") or check.get("error", "") or check.get("message", "")
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

    hw = data.get("hardware") or {}
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
        source = t.get("source") or "built-in"
        table.add_row(t.get("name", ""), t.get("desc") or t.get("description", ""), source)
    console.print(table)


def agents_table(official: list[dict], dynamic: list[dict]) -> None:
    """Display agents (official + dynamic)."""
    console.print(f"\n[bold]Agents officiels ({len(official)})[/bold] — Protégés")
    for a in official:
        icon = "[green]●[/green]" if a.get("enabled", True) else "[red]○[/red] (désactivé)"
        console.print(f"  {icon} [bold]{a.get('name', '?')}[/bold]  [dim]{a.get('description', '')}[/dim]")

    if dynamic:
        console.print(f"\n[bold]Agents dynamiques ({len(dynamic)})[/bold] — Sauvegardés")
        for a in dynamic:
            console.print(f"  [magenta]◆[/magenta] [bold]{a.get('name', '?')}[/bold]  [dim]{a.get('role', '')}[/dim]")
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
        table.add_row(s.get("id", "?"), (s.get("created_at", "") or "")[:19], str(s.get("message_count", 0)),
                      s.get("permissions", ""))
    console.print(table)


def permissions_display(levels: dict, current: str = "") -> None:
    """Display permission levels."""
    table = Table(title="Permission Levels", show_header=True, border_style="cyan")
    table.add_column("Level", style="bold")
    table.add_column("Capabilities")
    for name, perms in levels.items():
        if not isinstance(perms, dict):
            active = []
        else:
            active = [k for k, v in perms.items() if v]
        marker = " ← current" if name == current else ""
        highlight = "[green]" if name == current else ""
        row_name = f"{highlight}{name}{marker}{'[/green]' if name == current else ''}"
        table.add_row(row_name, ", ".join(active[:6]) + ("..." if len(active) > 6 else ""))
    console.print(table)


def skills_table(skills: list[dict]) -> None:
    """Display skills."""
    table = Table(title="Skills", show_header=True, border_style="cyan")
    table.add_column("Name", style="bold")
    table.add_column("Level")
    table.add_column("Description")
    table.add_column("Triggers", style="dim")
    for s in skills:
        triggers = (s.get("triggers") or [])[:3]
        table.add_row(s.get("name", ""), s.get("level", ""),
                      s.get("description", ""), ", ".join(triggers))
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
        table.add_row(c.get("name") or c.get("service", ""), icon,
                      ", ".join(c.get("capabilities") or []))
    console.print(table)


def code_diff(filename: str, diff_lines: list[str]) -> None:
    """Affiche de manière stylisée un diff de code (ajout/suppression)."""
    console.print(f"\n[bold bright_magenta]⚡ Modification de : {filename}[/bold bright_magenta]")
    table = Table(show_header=False, box=None, padding=(0, 1), collapse_padding=True)
    table.add_column("Ligne", justify="right", style="dim", width=4)
    table.add_column("Contenu")

    consumed = 0
    for line in diff_lines:
        line_clean = line.rstrip("\n")
        if line_clean.startswith("@@") or line_clean.startswith("\\ No newline"):
            consumed = 0
            table.add_row(" ", f"[cyan]{line_clean}[/cyan]")
        elif line_clean.startswith("+"):
            table.add_row(str(consumed), f"[bold green]{line_clean}[/bold green]")
            consumed += 1
        elif line_clean.startswith("-"):
            table.add_row(str(consumed), f"[bold red]{line_clean}[/bold red]")
            consumed += 1
        else:
            table.add_row(str(consumed), f"[dim]{line_clean}[/dim]")
            consumed += 1

    console.print(Panel(table, border_style="magenta", expand=False))
    console.print()


def ask_password(prompt: str) -> str:
    """Demande un mot de passe à l'utilisateur de manière sécurisée (masqué)."""
    from rich.prompt import Prompt
    console.print(f"\n[bold yellow]🔒 Autorisation requise[/bold yellow]")
    return Prompt.ask(f"[bold cyan]{prompt}[/bold cyan]", password=True)
