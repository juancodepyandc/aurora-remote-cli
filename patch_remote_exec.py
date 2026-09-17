import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

# We need to insert the handler for remote_command, remote_read_file, remote_write_file inside the SSE loop.
# We will insert it right before: elif etype == "error":

remote_handler = """            elif etype == "remote_command":
                cmd = event.get("command", "")
                cwd = event.get("cwd", "")
                display.console.print(f"\\n[bold yellow]⚡ Exécution locale (Mac):[/bold yellow] [cyan]{cmd}[/cyan]")
                try:
                    import subprocess
                    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=120)
                    out = result.stdout + "\\n" + result.stderr
                    if not out.strip(): out = "Commande réussie sans sortie."
                except Exception as e:
                    out = f"Erreur d'exécution locale: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_command_result", "value": out})

            elif etype == "remote_read_file":
                path = event.get("path", "")
                display.console.print(f"\\n[bold yellow]📖 Lecture locale (Mac):[/bold yellow] [dim]{path}[/dim]")
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        out = f.read()
                except Exception as e:
                    out = f"Erreur de lecture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_read_result", "value": out})

            elif etype == "remote_write_file":
                path = event.get("path", "")
                content = event.get("content", "")
                display.console.print(f"\\n[bold yellow]💾 Écriture locale (Mac):[/bold yellow] [dim]{path}[/dim]")
                try:
                    import os
                    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)
                    out = "Fichier écrit avec succès sur le Mac."
                except Exception as e:
                    out = f"Erreur d'écriture: {e}"
                client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "remote_write_result", "value": out})

"""

code = code.replace('            elif etype == "error":', remote_handler + '            elif etype == "error":')

with open(fp, "w", encoding="utf-8") as f:
    f.write(code)

print("Mission CLI patched!")
