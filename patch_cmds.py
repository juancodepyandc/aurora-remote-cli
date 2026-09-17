import re

file_path = "/home/juan/aurora-remote-cli/aurora_cli/interactive.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# Replace _cmd_permissions to be interactive
new_perm = """def _cmd_permissions(client, user_input: str) -> None:
    parts = user_input.split()
    if len(parts) > 1:
        level = parts[1].upper()
    else:
        try:
            data = client.permissions_get()
            display.permissions_display(data.get("levels", {}))
            console.print("[cyan]Entrez le nom de la permission (ex: AUTONOMOUS, SAFE) ou laissez vide pour annuler :[/cyan]")
            level = input("Nouvelle permission > ").strip().upper()
            if not level:
                return
        except Exception as e:
            display.error(str(e))
            return
            
    try:
        result = client.permissions_set(level)
        if result.get("ok"):
            display.success(f"Permissions: {level}")
        else:
            display.error(result.get("error", "Failed"))
    except Exception as e:
        display.error(str(e))
"""

code = re.sub(r'def _cmd_permissions\(client: AuroraClient, user_input: str\) -> None:.*?def _cmd_agents', new_perm + '\n\ndef _cmd_agents', code, flags=re.DOTALL)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Commands patched")
