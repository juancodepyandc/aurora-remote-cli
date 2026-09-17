import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

old_code = '    display.success(f"Mission {mission_id[:8]} démarrée. Analyse de la requête en cours...")'

new_code = """    from rich.panel import Panel
    from rich.text import Text
    import os
    
    actual_ws = workspace or os.getcwd()
    is_remote = not actual_ws.startswith("/home/") and not actual_ws.startswith("/tmp/")
    
    info_text = Text()
    info_text.append("🚀 Mission : ", style="bold green")
    info_text.append(f"{mission_id}\\n")
    info_text.append("📂 Confinement : ", style="bold blue")
    info_text.append(f"{actual_ws}\\n")
    info_text.append("🔌 Exécution : ", style="bold magenta")
    info_text.append(f"{'Locale (Mac/Win)' if is_remote else 'Distante (Linux)'} - Permissions: {permissions}\\n\\n")
    info_text.append("Analyse de la requête en cours...", style="dim italic")
    
    display.console.print(Panel(info_text, title="[bold cyan]✧ Aurora-IA Initialisation[/bold cyan]", border_style="cyan"))"""

if old_code in code:
    code = code.replace(old_code, new_code)
else:
    print("WARNING: Could not find old code block!")

with open(fp, "w", encoding="utf-8") as f:
    f.write(code)

print("Mission UI patched!")
