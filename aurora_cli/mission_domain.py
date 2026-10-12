"""Label the task domain separately from the terminal's dialogue tab."""
import re
import unicodedata

def domain_label(goal):
    text=unicodedata.normalize('NFKD',goal.casefold()).encode('ascii','ignore').decode()
    return '3D' if re.search(r'\b(?:3d|stl|glb|gltf|figurine|figurines|maillage)\b',text) else 'Mission'
