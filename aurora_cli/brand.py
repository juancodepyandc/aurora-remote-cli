"""Single source of truth for product identity.

Every user-facing string must come from here. Nothing in the codebase is
allowed to hardcode a product name: the previous build drifted into four
different names (package, prompt, log prefix, engine) and that is what this
module exists to prevent.

Two hard rules enforced by `tests/test_brand.py`:

1. No module outside this file may contain the legacy names.
2. ``APP_*`` constants are the only way to reach the product name.
"""
from __future__ import annotations

# --- Identity -------------------------------------------------------------

#: Human readable product name.
APP_NAME = "JOBIA"

#: Expanded name, used in banners and about text.
APP_FULL_NAME = "Juan Of Bike IA"

#: One-line pitch shown in the banner.
APP_TAGLINE = "Pilotage local et distant des modèles"

#: Slug used for directories, config keys and env vars.
APP_SLUG = "jobia"

#: Console script entry points, in priority order.
APP_COMMANDS = ("jobia", "jbia")

#: Legacy names that must never reappear in user-facing output.
LEGACY_NAMES = ("aurora", "nexus")

# --- Naming helpers -------------------------------------------------------


def display_name() -> str:
    """Return the product name for display."""
    return APP_NAME


def ascii_logo_text() -> str:
    """Plain-ASCII fallback for terminals without block-drawing support."""
    return f"{APP_NAME} - {APP_TAGLINE}"


def script_preferred() -> str:
    """Return the command the user should type."""
    return APP_COMMANDS[0]


# --- Environment variable prefix ------------------------------------------

ENV_PREFIX = "JOBIA_"
LEGACY_PREFIX = "AURORA_"


def env(name: str) -> str:
    """Build a namespaced environment variable name.

    >>> env("SERVER_URL")
    'JOBIA_SERVER_URL'
    """
    return f"{ENV_PREFIX}{name.upper()}"


def env_legacy(name: str) -> str:
    """Build the pre-rename variable name, read for backwards compatibility.

    >>> env_legacy("SERVER_URL")
    'AURORA_SERVER_URL'
    """
    return f"{LEGACY_PREFIX}{name.upper()}"


# --- Narrative copy -------------------------------------------------------

WELCOME = f"{APP_NAME} · {APP_TAGLINE}"
REMOTE_LABEL = "Serveur"
LOCAL_LABEL = "Local"
AUTODETECT_LABEL = "Auto"
NOT_CONFIGURED_HINT = f"Lance « {APP_COMMANDS[0]} connect » pour enregistrer un serveur distant."

#: Words used instead of the retired operational vocabulary.
RUNTIME_WORDS = {
    "reflect": "Réflexion",
    "execute": "Exécution",
    "analyze": "Analyse",
    "stream": "Flux",
    "agents": "Agents",
}
