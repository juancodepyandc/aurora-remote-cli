import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

old_code = """                # Routage universel vers les Téléchargements selon l'OS (Mac/Android/iOS)
                import platform
                
                out_dir = Path(os.getcwd()) # Fallback
                
                # Détection Android (Termux)
                if "com.termux" in os.environ.get("PREFIX", ""):
                    out_dir = Path("/storage/emulated/0/Download")
                # Détection iOS (a-Shell)
                elif "APPDIR" in os.environ and "a-Shell" in os.environ["APPDIR"]:
                    out_dir = Path.home()  # a-Shell home (Documents)
                # Détection Mac/Win
                else:
                    dl = Path.home() / "Downloads"
                    dl_fr = Path.home() / "Téléchargements"
                    if dl.exists(): out_dir = dl
                    elif dl_fr.exists(): out_dir = dl_fr
                
                # Ensure it exists
                out_dir.mkdir(parents=True, exist_ok=True)
                out_path = out_dir / filename"""

new_code = """                import platform
                
                # Détection Android (Termux)
                if "com.termux" in os.environ.get("PREFIX", ""):
                    out_dir = Path("/storage/emulated/0/Download")
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / filename
                # Détection iOS (a-Shell)
                elif "APPDIR" in os.environ and "a-Shell" in os.environ["APPDIR"]:
                    out_dir = Path.home()  # a-Shell home (Documents)
                    out_dir.mkdir(parents=True, exist_ok=True)
                    out_path = out_dir / filename
                # Détection Mac/Windows/Linux (Comportement normal : dossier actuel)
                else:
                    actual_ws = workspace or os.getcwd()
                    out_path = Path(actual_ws) / filename"""

if old_code in code:
    code = code.replace(old_code, new_code)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(code)
    print("Patched file transfer to revert desktop behavior while keeping mobile Downloads!")
else:
    print("WARNING: Could not find old_code!")
