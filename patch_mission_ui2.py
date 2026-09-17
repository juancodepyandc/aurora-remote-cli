import re

file_path = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

start_marker = "    try:\n        for event in client.mission_stream(mission_id):"
end_marker = "    except KeyboardInterrupt:"

start_idx = code.find(start_marker)
end_idx = code.find(end_marker)

new_loop = """    from rich.status import Status
    status_spinner = None

    try:
        for event in client.mission_stream(mission_id):
            etype = event.get("type", "")
            
            if etype == "step_start":
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
                display.token_print(token)
                
            elif etype == "file_diff":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                filename = event.get("filename", "unknown")
                diff_lines = event.get("diff", [])
                if token_buffer:
                    display.console.print("\\n")
                    token_buffer = ""
                display.code_diff(filename, diff_lines)
                
            elif etype == "sudo_request":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                reason = event.get("reason", "Action nécessite des privilèges élevés")
                if token_buffer:
                    display.console.print("\\n")
                    token_buffer = ""
                pwd = display.ask_password(f"{reason}. Mot de passe sudo :")
                try:
                    client.post(f"/api/cli/mission/{mission_id}/input", data={"input_type": "password", "value": pwd})
                    display.success("Mot de passe transmis et supprimé localement.")
                except Exception as e:
                    display.error(f"Erreur d'envoi du mot de passe : {e}")

            elif etype == "file_transfer":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                import base64
                from pathlib import Path
                filename = event.get("filename", "downloaded_file")
                b64data = event.get("data", "")
                
                desktop = Path.home() / "Desktop"
                if not desktop.exists(): desktop = Path.home() / "Bureau"
                if not desktop.exists(): desktop = Path.home()
                    
                out_path = desktop / filename
                if token_buffer:
                    display.console.print("\\n")
                    token_buffer = ""
                try:
                    out_path.write_bytes(base64.b64decode(b64data))
                    display.success(f"Fichier reçu et enregistré sur votre Mac : {out_path}")
                except Exception as e:
                    display.error(f"Erreur lors de l'enregistrement du fichier : {e}")

            elif etype == "error":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                display.error(event.get("message", "Une erreur est survenue."))
                
            elif etype == "mission_complete":
                if status_spinner:
                    status_spinner.stop()
                    status_spinner = None
                if token_buffer:
                    display.console.print("\\n")
                    token_buffer = ""
                display.mission_summary(event)
                break
                
"""

code = code[:start_idx] + new_loop + code[end_idx:]

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Mission UI patched 2")
