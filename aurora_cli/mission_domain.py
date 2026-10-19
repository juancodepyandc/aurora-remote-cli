"""Label the task domain separately from the terminal's dialogue tab."""
from __future__ import annotations

import re
import unicodedata


def domain_label(goal: str) -> str:
    text = unicodedata.normalize('NFKD', goal.casefold()).encode('ascii', 'ignore').decode()
    if re.search(r'\b(?:3d|stl|glb|gltf|figurine|figurines|maillage)\b', text):
        return '3D'
    return 'Mission'

