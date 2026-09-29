"""Read only selected source files from the verified upstream revision."""
from pathlib import PurePosixPath
import hashlib
import json
import httpx
from . import locations

REPOSITORY = 'juancodepyandc/juan-of-bike-ia-linux'
BRANCH = 'refonte/module-code'
REVISION = '0baabd81babb0f07509befd0e80ff6c7fbb4e9a2'


def fetch_source(name):
    path = PurePosixPath(name)
    if (path.is_absolute() or '..' in path.parts or not path.parts
            or any(part.startswith('.') for part in path.parts)
            or path.suffix not in {'.py', '.ts', '.tsx', '.rs', '.toml'}):
        raise ValueError('Indique un fichier source relatif ; secrets, documents et dossiers cachés sont exclus.')
    root = locations.data_dir() / 'upstream' / REVISION
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Destination hors du cache de référence.')
    if target.exists():
        return target
    url = f'https://raw.githubusercontent.com/{REPOSITORY}/{REVISION}/{path.as_posix()}'
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError('Fichier trop volumineux pour une référence de code.')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(response.content)
    target.with_suffix(target.suffix + '.source.json').write_text(json.dumps({
        'url': url, 'branch': BRANCH, 'revision': REVISION,
        'sha256': hashlib.sha256(response.content).hexdigest(),
    }, indent=2))
    return target
