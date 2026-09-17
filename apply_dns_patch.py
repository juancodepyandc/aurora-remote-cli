import re

fp = "/home/juan/aurora-remote-cli/aurora_cli/cli.py"
with open(fp, "r", encoding="utf-8") as f:
    code = f.read()

patch = """
# --- PATCH ANTI-CENSURE DNS (Box FR / NXDOMAIN Errno 8) ---
import socket
import httpx

_orig_getaddrinfo = socket.getaddrinfo

def _patched_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    if host.endswith(".trycloudflare.com"):
        try:
            # Resolution via DNS-over-HTTPS (Cloudflare) pour contourner le blocage FAI
            r = httpx.get(f"https://cloudflare-dns.com/dns-query?name={host}&type=A", headers={"accept": "application/dns-json"}, timeout=5.0)
            if r.status_code == 200:
                answers = r.json().get("Answer", [])
                if answers:
                    ip = answers[0]["data"]
                    return _orig_getaddrinfo(ip, port, family, type, proto, flags)
        except Exception:
            pass
    return _orig_getaddrinfo(host, port, family, type, proto, flags)

socket.getaddrinfo = _patched_getaddrinfo
# ---------------------------------------------------------
"""

if "PATCH ANTI-CENSURE" not in code:
    # Insert right after the imports
    code = code.replace('import click', patch + '\nimport click')
    with open(fp, "w", encoding="utf-8") as f:
        f.write(code)
    print("Patch applied!")
else:
    print("Patch already exists!")
