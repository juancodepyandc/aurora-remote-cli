import re
file_path = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# Add heartbeat handling
old_heart = "            elif etype == \"error\":"
new_heart = """            elif etype == "heartbeat":
                if status_spinner:
                    elapsed = event.get("elapsed", 0)
                    mins = int(elapsed) // 60
                    secs = int(elapsed) % 60
                    time_str = f"[{mins:02d}:{secs:02d}]"
                    status_spinner.update(f"[bold cyan]En cours :[/bold cyan] [white]{current_step}[/white] [dim]{time_str}[/dim]")
                    
            elif etype == "error":"""

code = code.replace(old_heart, new_heart)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Client timer patched")
