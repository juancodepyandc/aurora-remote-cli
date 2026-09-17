import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/cli.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

# Add a debug print right before connecting
code = code.replace(
    'console.print("Vérification et enregistrement...")',
    'console.print("Vérification et enregistrement...")\n        console.print(f"DEBUG URL: \'{url}\'")'
)

with open(fp, "w", encoding="utf-8") as f:
    f.write(code)

print("Debug added!")
