import re
file_path = "/home/juan/aurora-remote-cli/aurora_cli/cli.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

target = 'run_mission(client, request, permissions=config.get("default_permissions", "AUTONOMOUS"))'
replacement = 'import os\\n    run_mission(client, request, workspace=os.getcwd(), permissions=config.get("default_permissions", "AUTONOMOUS"))'

code = code.replace(target, replacement)

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("CLI patched")
