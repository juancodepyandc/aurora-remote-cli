"""Small, dependency-free fuzzy ranking shared by search features.

Used by the command palette, ``jobia skills search`` and ``jobia mcp search``.
Accents and case are ignored ("prévisualiser" matches "previsualiser"). Every
query word counts: an item matching all of them ranks above one matching only
some (coverage weighting), and a match in a name weighs more than one in a
description or a body. A few common French words also look for their English
counterpart, since most published skills and MCP tools are written in English.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")
_WORD = re.compile(r"[a-z0-9]+")

SYNONYMS = {
    "tableur": ("spreadsheet", "xlsx", "excel"), "classeur": ("spreadsheet", "xlsx", "workbook"),
    "excel": ("spreadsheet", "xlsx"), "feuille": ("sheet", "spreadsheet"), "formule": ("formula",),
    "formules": ("formulas", "formula"), "diaporama": ("presentation", "slides", "pptx"),
    "presentation": ("presentation", "slides", "pptx", "deck"), "diapositive": ("slide", "slides"),
    "diapositives": ("slides",), "document": ("document", "docx"), "word": ("docx", "document"),
    "pdf": ("pdf",), "image": ("image", "picture"), "photo": ("image", "photo"),
    "dessin": ("drawing", "design", "art"), "affiche": ("poster", "design"), "graphique": ("chart", "graph"),
    "navigateur": ("browser", "web"), "courriel": ("email", "mail"), "fichier": ("file",),
    "fichiers": ("files", "file"), "recherche": ("search",), "chercher": ("search", "find"),
    "base": ("database",), "donnees": ("data", "database"), "tableau": ("table", "chart"),
    "theme": ("theme",), "outil": ("tool",), "outils": ("tools",), "serveur": ("server",),
    "creer": ("create", "build"), "creation": ("create", "build"), "lire": ("read",), "ecrire": ("write",),
    "traduire": ("translate",), "resume": ("summary", "summarize"), "carte": ("map",),
    "audio": ("audio", "sound"), "voix": ("voice", "speech"), "video": ("video",), "jeu": ("game",),
    "site": ("website", "web"), "test": ("test", "testing"), "tests": ("testing", "test"),
}


def normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", str(text or ""))
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(_WORD.findall(stripped.lower()))


def _subsequence(needle: str, haystack: str) -> float:
    """Density of an in-order character match, 0 when absent."""
    position, first = 0, -1
    for ch in needle:
        position = haystack.find(ch, position)
        if position < 0:
            return 0.0
        if first < 0:
            first = position
        position += 1
    span = position - first
    return len(needle) / span if span else 0.0


def _token_score(token: str, field: str, words: Sequence[str]) -> float:
    if not field:
        return 0.0
    if token in words:
        return 3.0
    if any(word.startswith(token) for word in words):
        return 2.2
    if token in field:
        return 1.4
    if len(token) >= 3:
        density = _subsequence(token, field)
        if density >= 0.5:
            return 0.6 * density
    return 0.0


def score(query: str, fields: Sequence[tuple[str, float]]) -> float:
    """Score ``query`` against weighted ``(text, weight)`` fields; 0 = no match."""
    tokens = normalize(query).split()
    if not tokens:
        return 1.0
    prepared = [(normalize(text), weight) for text, weight in fields]
    prepared = [(text, text.split(), weight) for text, weight in prepared]
    total, matched = 0.0, 0
    for token in tokens:
        variants = (token, *SYNONYMS.get(token, ()))
        best = max((_token_score(variant, text, words) * weight * (1.0 if variant == token else 0.8)
                    for variant in variants for text, words, weight in prepared), default=0.0)
        if best > 0:
            matched += 1
            total += best
    if not matched:
        return 0.0
    # Partial matches stay findable but rank below items matching every word.
    total *= (matched / len(tokens)) ** 1.5
    whole = " ".join(tokens)
    if prepared and whole in prepared[0][0]:
        total += 2.0 * prepared[0][2]
        if prepared[0][0] == whole:
            total += 4.0 * prepared[0][2]
    return total


def rank(query: str, items: Iterable[T], fields: Callable[[T], Sequence[tuple[str, float]]],
         *, limit: int | None = 20) -> list[tuple[float, T]]:
    """Items sorted by decreasing score; ties keep their original order."""
    scored = [(score(query, fields(item)), index, item) for index, item in enumerate(items)]
    hits = sorted(((s, i, item) for s, i, item in scored if s > 0), key=lambda row: (-row[0], row[1]))
    rows = [(s, item) for s, _, item in hits]
    return rows if limit is None else rows[:limit]
