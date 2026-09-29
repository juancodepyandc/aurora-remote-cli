"""Entry point for ``python -m aurora_cli``.

The ``if __name__ == "__main__"`` guard is required, not stylistic: without it,
importing this module runs the CLI, which breaks test collection and any tool
that walks the package looking for importable modules.
"""
from aurora_cli.cli import main

if __name__ == "__main__":
    main()
