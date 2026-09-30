#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'
JOBIA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3 est requis pour installer JOBIA." >&2
  exit 1
fi
"$PYTHON_BIN" -m venv "$JOBIA_ROOT/.venv"
"$JOBIA_ROOT/.venv/bin/python" -m pip install --upgrade "$JOBIA_ROOT"
mkdir -p "$HOME/.local/bin"
ln -sf "$JOBIA_ROOT/.venv/bin/jobia" "$HOME/.local/bin/jobia"
ln -sf "$JOBIA_ROOT/.venv/bin/jbia" "$HOME/.local/bin/jbia"
"$HOME/.local/bin/jobia" --version >/dev/null
echo 'JOBIA installé et vérifié. Tu peux maintenant lancer : jobia'
