import os
from pathlib import Path

# Répertoire racine caché de l'application (type ~/.aurora-cli)
APP_DIR = Path(os.path.expanduser("~/.aurora-cli"))

# Sous-dossiers architecturés
BRAIN_DIR = APP_DIR / "brain"
LOGS_DIR = APP_DIR / "logs"
CONFIG_DIR = APP_DIR / "config"

# Fichiers spécifiques
SQLITE_DB_PATH = BRAIN_DIR / "jobia_memory.db"
CHROMA_DB_PATH = BRAIN_DIR / "vector_memory"
AGI_LOG_PATH = LOGS_DIR / "agi_daemon.log"

def init_app_dirs():
    """Crée l'architecture cachée si elle n'existe pas."""
    for directory in [APP_DIR, BRAIN_DIR, LOGS_DIR, CONFIG_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
