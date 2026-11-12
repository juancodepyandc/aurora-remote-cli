"""Visual primitives shared by the Python and native interfaces.

Everything here is pure data or a pure function of (state, capabilities,
elapsed time), so that every animated element of a screen advances on the
same clock: the connection badge, the activity spinner and the pairing
countdown move together, and only while real work is happening.
"""
from __future__ import annotations

SPINNERS = {
    "dots": ("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏", "|/-\\"),
    "arc": ("◜◠◝◞◡◟", "-\\|/"),
    "wave": ("▁▂▃▄▅▆▇█▇▆▅▄▃▂", "_.-~"),
    "pulse": ("█▉▊▋▌▍▎▏▎▍▌▋▊▉", "#*+."),
}
#: Seconds per frame. ``reduced`` keeps a calm 4 Hz, ``none`` is static.
FRAME_SECONDS = {"full": 0.1, "reduced": 0.25}

#: state → (unicode glyph, ascii glyph, palette tone, animated)
BADGES = {
    "online": ("●", "*", "success", False),
    "degraded": ("◐", "~", "warning", False),
    "reconnecting": ("", "", "warning", True),
    "checking": ("", "", "info", True),
    "offline": ("○", "o", "error", False),
    "local": ("◆", "#", "accent", False),
    "unpaired": ("◇", "?", "warning", False),
    "pairing": ("", "", "accent", True),
}

BIG_DIGITS = {
    "0": ("█▀█", "█ █", "▀▀▀"), "1": ("▀█ ", " █ ", "▀▀▀"), "2": ("▀▀█", "█▀▀", "▀▀▀"),
    "3": ("▀▀█", " ▀█", "▀▀▀"), "4": ("█ █", "▀▀█", "  ▀"), "5": ("█▀▀", "▀▀█", "▀▀▀"),
    "6": ("█▀▀", "█▀█", "▀▀▀"), "7": ("▀▀█", "  █", "  ▀"), "8": ("█▀█", "█▀█", "▀▀▀"),
    "9": ("█▀█", "▀▀█", "▀▀▀"),
}

KIND_ICONS = {"commande": ("›", ">"), "vue": ("▣", "#"), "thème": ("◐", "~"), "skill": ("✦", "*"),
              "outil MCP": ("⚙", "%"), "serveur MCP": ("◈", "@"), "modèle": ("◆", "+")}


def frames(kind: str, unicode: bool) -> str:
    pair = SPINNERS.get(kind, SPINNERS["dots"])
    return pair[0] if unicode else pair[1]


def spinner(kind: str, *, unicode: bool, animation: str, elapsed: float) -> str:
    cycle = frames(kind, unicode)
    step = FRAME_SECONDS.get(animation)
    if not step:
        return cycle[0]
    return cycle[int(max(0.0, elapsed) / step) % len(cycle)]


def badge(state: str, *, unicode: bool, animation: str, elapsed: float, spinner_kind: str = "dots"):
    """``(glyph, tone)`` of the connection badge for a normalised state."""
    glyph_u, glyph_a, tone, animated = BADGES.get(state, BADGES["checking"])
    if animated:
        return spinner(spinner_kind, unicode=unicode, animation=animation, elapsed=elapsed), tone
    return (glyph_u if unicode else glyph_a), tone


def big_code(code: str, unicode: bool) -> list[str]:
    """Six digits drawn three rows high, grouped 3 + 3 for easy copying."""
    digits = [c for c in str(code) if c.isdigit()][:12]
    if not unicode:
        spaced = " ".join(digits)
        return [spaced[:5] + "   " + spaced[6:] if len(digits) == 6 else spaced]
    rows = ["", "", ""]
    for index, digit in enumerate(digits):
        gap = "   " if index == 3 and len(digits) == 6 else (" " if index else "")
        for row in range(3):
            rows[row] += gap + BIG_DIGITS[digit][row]
    return rows


def progress_cells(done: int, total: int, width: int, unicode: bool) -> tuple[str, str]:
    """Filled and empty parts of a bar; the caller colours them."""
    width = max(4, width)
    filled = 0 if total <= 0 else round(width * max(0, min(done, total)) / total)
    full, empty = ("█", "░") if unicode else ("#", "-")
    return full * filled, empty * (width - filled)


def keycap(key: str, unicode: bool) -> str:
    return f" {key} " if unicode else f"[{key}]"


def palette_items(app) -> list[dict]:
    """Everything reachable from the command palette, most useful first."""
    from aurora_cli import config, themes
    from aurora_cli.ui import PAGE_LABELS, SLASH_COMMANDS_CATALOG
    items: list[dict] = []
    needs_argument = {"/resume", "/attach", "/skill", "/connect"}
    for command, _, badge_label, description in SLASH_COMMANDS_CATALOG:
        items.append({"kind": "commande", "label": command, "hint": description,
                      "action": "insert" if command in needs_argument else "submit",
                      "value": command + (" " if command in needs_argument else "")})
    for page, label in PAGE_LABELS.items():
        items.append({"kind": "vue", "label": label, "hint": "Afficher la vue " + label,
                      "action": "view", "value": page})
    for theme in themes.all_themes():
        items.append({"kind": "thème", "label": f"{theme.label} ({theme.id})", "hint": theme.description,
                      "action": "theme", "value": theme.id})
    active = set(getattr(app, "active_skills", []) or [])
    for row in getattr(app, "skill_rows", [])[:150]:
        name = row.get("name", "")
        items.append({"kind": "skill", "label": name,
                      "hint": ("actif · retirer" if name in active else "activer") + " · " + row.get("description", "")[:120],
                      "action": "submit", "value": "/skill " + name})
    for row in getattr(app, "mcp_rows", [])[:40]:
        items.append({"kind": "serveur MCP", "label": row.get("name", ""), "hint": row.get("describe", "")[:120],
                      "action": "view", "value": "mcp"})
        for tool in row.get("tools", [])[:40]:
            items.append({"kind": "outil MCP", "label": f"{row.get('name', '')}:{tool}",
                          "hint": "Appeler avec des arguments JSON",
                          "action": "insert", "value": f"/mcp appel {row.get('name', '')} {tool} {{}}"})
    for model in dict.fromkeys(getattr(app, "models", []) or []):
        items.append({"kind": "modèle", "label": model,
                      "hint": "modèle actuel" if model == config.get("default_model", "") else "utiliser ce modèle",
                      "action": "model", "value": model})
    return items[:600]
