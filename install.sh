#!/usr/bin/env bash
# Thin shim. The install logic lives in aurora_cli/install.py so that macOS,
# Linux and Windows all run the same steps; this file only picks a Python.
set -euo pipefail
JOBIA_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
for CANDIDATE in "${PYTHON_BIN:-}" python3 python; do
  [ -n "$CANDIDATE" ] || continue
  if command -v "$CANDIDATE" >/dev/null 2>&1; then
    if "$CANDIDATE" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' >/dev/null 2>&1; then
      exec "$CANDIDATE" "$JOBIA_ROOT/aurora_cli/install.py" --root "$JOBIA_ROOT" --receipt "$JOBIA_ROOT/install-receipt.json" "$@"
    fi
  fi
done
echo "Python 3.10 ou plus est requis pour installer JOBIA." >&2
exit 1
