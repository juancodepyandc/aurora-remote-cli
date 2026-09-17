import re
import os

fp = "/home/juan/aurora-remote-cli/aurora_cli/cli.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

# Replace httpx.get(gist_api_url) with a version that has cache busting
old_line = 'r_url = httpx.get(gist_api_url, timeout=10.0)'
new_line = 'import time\n        r_url = httpx.get(f"{gist_api_url}?_t={int(time.time())}", headers={"Cache-Control": "no-cache"}, timeout=10.0)'

code = code.replace(old_line, new_line)

with open(fp, "w", encoding="utf-8") as f:
    f.write(code)

print("Cache busting added!")
