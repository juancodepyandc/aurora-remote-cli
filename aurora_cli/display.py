"""Rich terminal display for Aurora CLI (PRO DESIGN)."""
from __future__ import annotations
from typing import Any
import time
import random

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markdown import Markdown
from rich.live import Live
from rich.spinner import Spinner
from rich.columns import Columns
from rich.align import Align
from rich.layout import Layout
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
        
    hw = status_data.get("hardware", {})
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
