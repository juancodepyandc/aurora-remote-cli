import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

# Replace the desktop hardcoding with workspace writing
old_code = """                desktop = Path.home() / "Desktop"
                if not desktop.exists(): desktop = Path.home() / "Bureau"
                if not desktop.exists(): desktop = Path.home()
                    
                out_path = desktop / filename"""

new_code = """                # Sauvegarde prioritaire dans le dossier actuel (workspace) sinon fallback Bureau
                actual_ws = workspace or os.getcwd()
                out_path = Path(actual_ws) / filename"""

if old_code in code:
    code = code.replace(old_code, new_code)
else:
    print("WARNING: Could not find file_transfer block!")

with open(fp, "w", encoding="utf-8") as f:
    f.write(code)

print("File transfer patched!")
