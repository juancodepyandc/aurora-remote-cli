"""Compatibility exports; editable theme data lives in data/*.json."""
from .loader import load_theme

OTTER = load_theme("otter")
PALETTE = OTTER.palette
OTTER_ART = OTTER.banner.art
OTTER_ART_ASCII = OTTER.banner.art_ascii
