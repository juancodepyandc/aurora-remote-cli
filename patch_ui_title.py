import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/mission.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

# Replace the mission ID logic with a truncated request string
old_code = """    info_text = Text()
    info_text.append("🚀 Mission : ", style="bold green")
    info_text.append(f"{mission_id}\\n")"""

new_code = """    info_text = Text()
    info_text.append("🚀 Mission : ", style="bold green")
    
    # Clean up request for display
    display_req = request.replace('\\n', ' ')
    if len(display_req) > 55:
        display_req = display_req[:55] + "..."
        
    info_text.append(f"{display_req}\\n")"""

if old_code in code:
    code = code.replace(old_code, new_code)
else:
    print("WARNING: Could not find old_code block!")

with open(fp, "w", encoding="utf-8") as f:
    f.write(code)

print("Mission UI title patched!")
