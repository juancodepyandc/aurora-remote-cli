"""Compatibility exports; editable theme data lives in data/*.json."""
from .loader import load_theme

JOBIA = load_theme("jobia")
PALETTE = JOBIA.palette
JOBIA_ART = JOBIA.banner.art
