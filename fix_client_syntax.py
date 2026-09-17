import re
import os

files = [
    "/home/juan/aurora-remote-cli/aurora_cli/cli.py",
    "/home/juan/aurora-remote-cli/aurora_cli/interactive.py"
]

for fp in files:
    if os.path.exists(fp):
        with open(fp, "r", encoding="utf-8") as f:
            code = f.read()
        
        # Replace literal \n string with actual newline
        code = code.replace(r'import os\n', 'import os\n')
        
        with open(fp, "w", encoding="utf-8") as f:
            f.write(code)

print("Client syntax fixed!")
