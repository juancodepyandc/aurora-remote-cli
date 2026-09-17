import re

file_path = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# Update the reflection start UI
target1 = 'spin = Spinner("dots", text=Text(f"▸ {step_name}...", style="dim"))'
replacement1 = 'spin = Spinner("bouncingBar", text=Text(f"✧ Aurora | {step_name}...", style="bold magenta"))'
code = code.replace(target1, replacement1)

# Update the reflection end UI
target2 = 'display.console.print(f"[dim]▸ {step_name} ({elapsed_step}s)[/dim]")'
replacement2 = 'display.console.print(f"[bold magenta]✧ Aurora[/bold magenta] [dim]| {step_name} ({elapsed_step}s)[/dim]")'
code = code.replace(target2, replacement2)

# Update the heartbeat UI
target3 = 'spin = Spinner("dots", text=Text(f"▸ {current_step} [{mins:02d}:{secs:02d}]...", style="dim"))'
replacement3 = 'spin = Spinner("bouncingBar", text=Text(f"✧ Aurora | {current_step} [{mins:02d}:{secs:02d}]...", style="bold magenta"))'
code = code.replace(target3, replacement3)

# Update the Bash UI
target4 = 'display.console.print(f"\\n[bold blue]●[/bold blue] [bold]Bash[/bold]([cyan]{cmd}[/cyan])")'
replacement4 = 'display.console.print(f"\\n[bold cyan]⚡[/bold cyan] [bold white]Système[/bold white] [dim]❯[/dim] [cyan]{cmd}[/cyan]")'
code = code.replace(target4, replacement4)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Aurora UI patched")
