import re

file_path = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

target = """    try:
        for event in client.mission_stream(mission_id):"""

replacement = """    # UI state
    from rich.live import Live
    from rich.spinner import Spinner
    from rich.text import Text
    import re
    
    live_spinner = None
    reflection_start = 0

    try:
        for event in client.mission_stream(mission_id):"""

code = code.replace(target, replacement)

target2 = """            if etype == "step_start":
                if status_spinner:
                    status_spinner.stop()
                step_name = event.get("step", "Processing")
                current_step = step_name
                
                # Start a beautiful status spinner for the step
                status_spinner = display.console.status(f"[bold cyan]En cours :[/bold cyan] [white]{step_name}[/white]", spinner="bouncingBar")
                status_spinner.start()
                
            elif etype == "step_end":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                elapsed = time.time() - start_time
                step_name = event.get("step", current_step)
                display.mission_step(step_name, event.get("index", 0), elapsed=elapsed, done=True)
                
            elif etype == "token":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                token = event.get("content", "")
                token_buffer += token
                display.token_print(token)"""

replacement2 = """            if etype == "step_start":
                step_name = event.get("step", "")
                current_step = step_name
                if "Réflexion" in step_name or "Exécution" in step_name:
                    if live_spinner:
                        live_spinner.stop()
                    reflection_start = time.time()
                    spin = Spinner("dots", text=Text(f"▸ {step_name}...", style="dim"))
                    live_spinner = Live(spin, refresh_per_second=10, console=display.console, transient=True)
                    live_spinner.start()
                
            elif etype == "step_end":
                step_name = event.get("step", current_step)
                if live_spinner and ("Réflexion" in step_name or "Exécution" in step_name):
                    live_spinner.stop()
                    live_spinner = None
                    elapsed_step = int(time.time() - reflection_start)
                    display.console.print(f"[dim]▸ {step_name} ({elapsed_step}s)[/dim]")
                elif "Action:" in step_name:
                    pass # Handled by the tokens usually, or we can ignore
                
            elif etype == "heartbeat":
                if live_spinner:
                    elapsed = event.get("elapsed", 0)
                    mins = int(elapsed) // 60
                    secs = int(elapsed) % 60
                    spin = Spinner("dots", text=Text(f"▸ {current_step} [{mins:02d}:{secs:02d}]...", style="dim"))
                    live_spinner.update(spin)
                    
            elif etype == "token":
                if live_spinner:
                    live_spinner.stop()
                    live_spinner = None
                token = event.get("content", "")
                # Custom formatting for Bash commands
                if "[EXECUTION BASH]:" in token:
                    cmd = token.replace("[EXECUTION BASH]:", "").strip()
                    display.console.print(f"\\n[bold blue]●[/bold blue] [bold]Bash[/bold]([cyan]{cmd}[/cyan])")
                else:
                    # Indent raw token output slightly for aesthetics
                    # If it has newlines, indent the next line
                    lines = token.split("\\n")
                    for i, line in enumerate(lines):
                        if i == len(lines) - 1:
                            display.console.print(f"  [dim]{line}[/dim]", end="", highlight=False)
                        else:
                            display.console.print(f"  [dim]{line}[/dim]", highlight=False)"""

code = re.sub(r'            if etype == "step_start":.*?display\.token_print\(token\)', replacement2, code, flags=re.DOTALL)

# Remove the old heartbeat logic since we replaced it
old_hb = """            elif etype == "heartbeat":
                if status_spinner:
                    elapsed = event.get("elapsed", 0)
                    mins = int(elapsed) // 60
                    secs = int(elapsed) % 60
                    time_str = f"[{mins:02d}:{secs:02d}]"
                    status_spinner.update(f"[bold cyan]En cours :[/bold cyan] [white]{current_step}[/white] [dim]{time_str}[/dim]")"""
code = code.replace(old_hb, "")

# Remove the other `if status_spinner: status_spinner.stop()` occurrences
code = code.replace("if status_spinner:", "if live_spinner:")
code = code.replace("status_spinner.stop()", "live_spinner.stop()")
code = code.replace("status_spinner = None", "live_spinner = None")

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Client UI aesthetic patch applied")
