"""Keep creation content separate from the requested delivery location."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys

from . import locations


@dataclass(frozen=True)
class CreationRequest:
    original: str
    subject: str
    output_dir: Path
    input_image: Path | None = None


def documents_dir() -> Path:
    """Resolve the user's actual Documents directory, including redirected ones."""
    if sys.platform == "win32":
        import ctypes
        buffer = ctypes.create_unicode_buffer(32768)
        # CSIDL_PERSONAL follows Windows folder redirection (e.g. OneDrive).
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buffer) == 0:
            return Path(buffer.value)
    if sys.platform.startswith("linux"):
        try:
            result = subprocess.run(["xdg-user-dir", "DOCUMENTS"], text=True,
                                    capture_output=True, timeout=5, check=True)
            path = Path(result.stdout.strip())
            if path.is_absolute():
                return path
        except (OSError, subprocess.SubprocessError):
            pass
    return Path.home() / "Documents"


# Destination grammar is deliberately scoped to delivery phrases: merely
# depicting "a cabinet containing documents" must not change the filesystem.
_DELIVERY = re.compile(
    r"(?:\s+(?:et|and)\s+|[,;]\s*|\s+)"
    r"(?:(?:mets?|place|enregistre|sauvegarde|exporte|save|put|export)"
    r"(?:\s+(?:le|la|les|it|them))?\s+)?"
    r"(?:dans|vers|sous|in|into|to)\s+"
    r"(?:(?:le|mon|mes|the|my)\s+)?(?:(?:dossier|répertoire|repertoire|folder|directory)\s+)?"
    r"(?P<destination>Documents\b(?:[/\\][^\n]+)?|[\"'][^\"'\n]+[\"']|(?:~[/\\]|/|[A-Za-z]:[/\\])[^\n]+)"
    r"\s*[.!]?\s*$", re.IGNORECASE,
)

# Destinations can precede the object: "génère moi dans Documents un modèle
# 3D ...". Quoted paths are unambiguous; bare paths in the middle are not.
_INLINE_DELIVERY = re.compile(
    r"\b(?:dans|vers|in|into|to)\s+"
    r"(?:(?:le|mon|mes|the|my)\s+)?(?:(?:dossier|folder|directory)\s+)?"
    r"(?P<destination>Documents\b(?![/\\])|[\"'][^\"'\n]+[\"'])",
    re.IGNORECASE,
)

# These are file formats, not subject/model-specific routing rules. Intake
# subsequently asks an actual decoder whether the file really is an image.
_IMAGE_EXTENSION = r"\.(?:png|jpe?g|webp|bmp|tiff?|tga|gif|avif|heic|heif|exr)"
_IMAGE_END = r"(?=$|[\s,;:!?]|\.(?:\s|$))"
_IMAGE_PATH = re.compile(
    r"(?P<quoted>\"[^\"\n]+" + _IMAGE_EXTENSION + r"\"|'[^'\n]+" + _IMAGE_EXTENSION + r"')"
    r"|(?<![\w:/\\])(?P<path>(?:~[/\\]|\.{1,2}[/\\]|/|[A-Za-z]:[/\\]|\\\\)"
    r"[^\n\"']*?" + _IMAGE_EXTENSION + r")" + _IMAGE_END,
    re.IGNORECASE,
)
_RELATIVE_IMAGE = re.compile(r"(?<![\w/\\])[^\s\"':,;]+" + _IMAGE_EXTENSION + _IMAGE_END,
                             re.IGNORECASE)
_EXPLICIT_REFERENCE = re.compile(
    r"\b(?:cette|ce|mon|ma|this|that|supplied|attached)\s+(?:image|photo|picture|reference|référence)\b"
    r"|\b(?:image|photo|référence|reference)\s+(?:jointe|joint|fournie|attached|supplied)\b",
    re.IGNORECASE,
)


def _input_image_span(subject: str):
    if re.search(r"\b(?:https?|file)://[^\s\"']+" + _IMAGE_EXTENSION, subject, re.I):
        raise ValueError("Les références par URL ne sont pas importées par ce pipeline ; "
                         "fournis le fichier image local. Aucune image de remplacement ne sera générée.")
    matches = list(_IMAGE_PATH.finditer(subject))
    # A bare filename is an input only in an explicit image conversion request;
    # a filename printed on the surface of an object is not a file attachment.
    if not matches and re.search(r"\b(?:image|photo|picture|reference|référence|from|depuis|convert|convertir|convertis)\b",
                                 subject, re.I):
        matches = list(_RELATIVE_IMAGE.finditer(subject))
    if len(matches) > 1:
        raise ValueError("Plusieurs images fournies : précise l’unique référence à reconstruire en 3D.")
    if not matches:
        return None
    match = matches[0]
    raw = match.group().strip('"\'')
    # Never reinterpret a foreign OS path as a relative path on this host.
    # This is an actionable error, not a reason to invent a replacement image.
    if os.name != 'nt' and re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", raw):
        raise ValueError("Chemin Windows inaccessible sur ce système ; fournis le chemin local de l’image.")
    return match, Path(os.path.expandvars(raw)).expanduser().resolve()


def parse_creation_request(request: str, output_dir: Path | None = None, *, capability: str = '3d',
                           input_image: Path | None = None) -> CreationRequest:
    if capability not in {'3d', 'image'}:
        raise ValueError('Type de création non pris en charge par ce parseur.')
    subject = request.strip()
    # Rejoin a copied terminal wrap only directly after a path separator.
    subject = re.sub(r"(?<=[/\\])\r?\n[ \t]*", "", subject)
    source = _input_image_span(subject) if capability == '3d' else None
    if source:
        span, parsed_image = source
        if input_image is not None and Path(input_image).expanduser().resolve() != parsed_image:
            raise ValueError("L’image indiquée dans la demande diffère de l’image d’entrée explicite.")
        input_image = parsed_image
        # Mask before parsing delivery, so 'from/to "picture.png"' cannot be
        # mistaken for an output directory. Indices remain unchanged.
        masked = subject[:span.start()] + ' ' * (span.end() - span.start()) + subject[span.end():]
    else:
        masked = subject
    if capability == '3d' and input_image is None and _EXPLICIT_REFERENCE.search(subject):
        raise ValueError("La demande désigne une image existante, mais aucun chemin d’image exploitable "
                         "n’a été reconnu. Fournis son chemin local entre guillemets ; "
                         "aucune référence de remplacement ne sera inventée.")
    match = _DELIVERY.search(masked) or _INLINE_DELIVERY.search(masked)
    destination = None
    if match:
        raw = subject[match.start('destination'):match.end('destination')].strip().strip('"\'')
        if raw.casefold() == "documents":
            destination = documents_dir()
        elif raw.lower().startswith(("documents/", "documents\\")):
            destination = documents_dir().joinpath(*re.split(r"[/\\]", raw)[1:])
        else:
            destination = Path(os.path.expandvars(raw)).expanduser()
    # Remove both spans together, without changing either set of offsets.
    spans = ([source[0].span()] if source else []) + ([match.span()] if match else [])
    for start, end in sorted(spans, reverse=True):
        subject = subject[:start] + ' ' + subject[end:]
    subject = re.sub(r"\s+", " ", subject).strip(" ,;:")
    subject = re.sub(
        r"^(?:génère|genere|crée|cree|fais|generate|create|make)\s+"
        r"(?:(?:moi|me)\s+)?(?:(?:un|une|a|an)\s+)?"
        r"(?:(?:modèle|modele|objet|maillage|model|mesh)\s+3d|3d\s+(?:model|mesh))"
        r"\s*(?:(?:de|du|d['’]|of)\s*)?", "", subject, flags=re.IGNORECASE,
    ).strip()
    if capability == 'image':
        subject = re.sub(r"^(?:génère|genere|crée|cree|generate|create|make)\s+"
                         r"(?:(?:moi|me)\s+)?(?:(?:un|une|a|an)\s+)?"
                         r"(?:image|illustration|portrait|picture)\s*(?:(?:de|of)\s*)?",
                         '', subject, flags=re.IGNORECASE).strip()
    if not subject and input_image is not None:
        subject = "Reconstruction 3D fidèle à l’image fournie"
    if not subject:
        raise ValueError("La demande doit décrire le modèle à créer.")
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()[:12]
    target = output_dir if output_dir is not None else (
        destination / "JOBIA" / (capability + "-" + digest) if destination is not None
        else locations.data_dir() / "outputs" / capability / digest
    )
    return CreationRequest(request, subject, Path(target).expanduser().resolve(),
                           Path(input_image).expanduser().resolve() if input_image is not None else None)
