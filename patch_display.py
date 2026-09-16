import re

with open("aurora_cli/display.py", "r") as f:
    content = f.read()

# I will find everything from "conns = status.get" to the end of that block and replace it with a clean banner function.
# Wait, I can just replace the whole file because I know what display.py looks like? No, it has many functions.

# Let's fix the broken part at the top.
fix = """
def banner(status_data: dict) -> None:
    \"\"\"Affiche une bannière futuriste et animée.\"\"\"
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
            content = f"{frame}\\n\\n[dim]Système distant synchronisé.[/dim]"
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
            "[bold white]A U R O R A   N E X U S[/bold white]\\n"
            "[bold cyan]───────────────────────────────────[/bold cyan]\\n"
            f"[dim]Serveur distant[/dim]  : [green]En ligne[/green]\\n"
            f"[dim]Puissance Brute[/dim]  : [cyan]{gpu_info}[/cyan]\\n"
            f"[dim]Mode Autonome[/dim]    : [magenta]Opérationnel[/magenta]\\n"
            "[bold cyan]───────────────────────────────────[/bold cyan]"
        )
        live.update(Panel(Align.center(final_content), border_style="bold blue", width=60))
        live.refresh()
    console.print()
"""

# Replace the broken fragment
broken_fragment_start = content.find("conns = status.get")
broken_fragment_end = content.find("def doctor_results")
if broken_fragment_start != -1 and broken_fragment_end != -1:
    content = content[:broken_fragment_start] + fix + "\n\n" + content[broken_fragment_end:]
    
with open("aurora_cli/display.py", "w") as f:
    f.write(content)
print("display.py patched")
