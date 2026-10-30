#!/usr/bin/env bash
# Thin shim. The install logic lives in aurora_cli/install.py so that macOS,
# Linux and Windows all run the same steps; this file locates or provisions
# an optimal Python interpreter before handing over to aurora_cli/install.py.
set -euo pipefail
JOBIA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
export JOBIA_INSTALL_CALLER_PATH="$PATH"
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

pick_python() {
  local min_minor="$1"
  local candidate
  for candidate in \
    "${PYTHON_BIN:-}" \
    "${JOBIA_PYTHON:-}" \
    "$JOBIA_ROOT/.venv/bin/python" \
    python3.13 python3.12 python3.11 python3.10 \
    /opt/homebrew/bin/python3.13 /opt/homebrew/bin/python3.12 /opt/homebrew/bin/python3.11 /opt/homebrew/bin/python3.10 /opt/homebrew/bin/python3 \
    /usr/local/bin/python3.13 /usr/local/bin/python3.12 /usr/local/bin/python3.11 /usr/local/bin/python3.10 /usr/local/bin/python3 \
    "$HOME/.local/bin/python3.13" "$HOME/.local/bin/python3.12" "$HOME/.local/bin/python3.11" "$HOME/.local/bin/python3.10" "$HOME/.local/bin/python3" \
    "$HOME/.pyenv/shims/python3" \
    "$HOME/miniconda3/bin/python" \
    "$HOME/anaconda3/bin/python" \
    python3 python; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 || [ -x "$candidate" ]; then
      if "$candidate" -c "import sys; raise SystemExit(sys.version_info < (3, $min_minor))" >/dev/null 2>&1; then
        command -v "$candidate" 2>/dev/null || echo "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

SELECTED_PYTHON=""
if SELECTED_PYTHON="$(pick_python 10)"; then
  :
else
  # Auto-provision an optimal standalone Python 3.12 via uv (no root required)
  # or platform package manager before falling back to system Python >= 3.8.
  if ! command -v uv >/dev/null 2>&1 && command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 || true
  fi
  if command -v uv >/dev/null 2>&1; then
    if uv python install 3.12 >/dev/null 2>&1; then
      SELECTED_PYTHON="$(uv python find 3.12 2>/dev/null || true)"
    fi
  fi
  if [ -z "$SELECTED_PYTHON" ] && [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
    brew install python@3.12 >/dev/null 2>&1 || true
    SELECTED_PYTHON="$(pick_python 10 || true)"
  fi
  if [ -z "$SELECTED_PYTHON" ]; then
    SELECTED_PYTHON="$(pick_python 8 || true)"
  fi
fi

if [ -n "$SELECTED_PYTHON" ]; then
  exec "$SELECTED_PYTHON" "$JOBIA_ROOT/aurora_cli/install.py" --python "$SELECTED_PYTHON" --root "$JOBIA_ROOT" --receipt "$JOBIA_ROOT/install-receipt.json" "$@"
fi

echo "Python 3 est requis pour installer JOBIA." >&2
exit 1

