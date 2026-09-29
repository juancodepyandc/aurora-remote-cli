#!/usr/bin/env bash
set -euo pipefail
JOBIA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
python3 -m venv "$JOBIA_ROOT/.venv"
"$JOBIA_ROOT/.venv/bin/python" -m pip install --upgrade "$JOBIA_ROOT"
mkdir -p "$HOME/.local/bin"
ln -sf "$JOBIA_ROOT/.venv/bin/jobia" "$HOME/.local/bin/jobia"
ln -sf "$JOBIA_ROOT/.venv/bin/jbia" "$HOME/.local/bin/jbia"
echo 'JOBIA installé. Lance ~/.local/bin/jobia'
echo 'Pour lancer jobia partout : ajoute ~/.local/bin au début de ton PATH.'
