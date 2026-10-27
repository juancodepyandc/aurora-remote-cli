"""Compatibility exports; editable theme data lives in data/*.json."""
from .loader import load_theme

ABYSS = load_theme("abyss")
PLAIN = load_theme("plain")
ABYSS_ART = ABYSS.banner.art
PLAIN_ART = PLAIN.banner.art
