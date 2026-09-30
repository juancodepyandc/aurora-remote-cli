"""Reversible resource actions before an expensive local generation."""
import shutil
import subprocess


def release_ollama_models() -> list[str]:
    binary = shutil.which("ollama")
    if not binary:
        return []
    try:
        result = subprocess.run([binary, "ps"], capture_output=True, text=True,
                                timeout=5, check=False)
    except OSError:
        return []
    tags = [line.split()[0] for line in (result.stdout or "").splitlines()[1:]
            if line.split()]
    for tag in tags:
        subprocess.run([binary, "stop", tag], capture_output=True, text=True,
                       timeout=30, check=False)
    return tags
