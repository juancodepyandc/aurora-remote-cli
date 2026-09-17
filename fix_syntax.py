import re
file_path = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(file_path, "r", encoding="utf-8") as f:
    code = f.read()

# Replace actual newlines inside the f-string
code = code.replace('display.console.print(f"\n[bold', 'display.console.print(f"\\n[bold')
code = code.replace('lines = token.split("\n")', 'lines = token.split("\\n")')

with open(file_path, "w", encoding="utf-8") as f:
    f.write(code)

print("Syntax fixed")
