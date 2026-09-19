import os
from pathlib import Path
import shutil

# --- Migration vers XDG Base Directory Specification ---
_xdg_data_home = os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
_xdg_config_home = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))

# Nouveaux chemins XDG
APP_DATA_DIR = Path(_xdg_data_home) / "aurora"
APP_CONFIG_DIR = Path(_xdg_config_home) / "aurora"

# Sous-dossiers architecturés
BRAIN_DIR = APP_DATA_DIR / "brain"
LOGS_DIR = APP_DATA_DIR / "logs"
CONFIG_DIR = APP_CONFIG_DIR

# Fichiers spécifiques
SQLITE_DB_PATH = BRAIN_DIR / "jobia_memory.db"
CHROMA_DB_PATH = BRAIN_DIR / "vector_memory"
AGI_LOG_PATH = LOGS_DIR / "agi_daemon.log"

def init_app_dirs():
    """Crée l'architecture cachée si elle n'existe pas et migre les anciens dossiers."""
    for directory in [APP_DATA_DIR, APP_CONFIG_DIR, BRAIN_DIR, LOGS_DIR]:
        directory.mkdir(parents=True, exist_ok=True)
        
    # Migration depuis l'ancien ~/.aurora-cli
    old_dir = Path(os.path.expanduser("~/.aurora-cli"))
    if old_dir.exists():
        if (old_dir / "brain").exists() and not BRAIN_DIR.exists():
            shutil.move(str(old_dir / "brain"), str(BRAIN_DIR))
        if (old_dir / "logs").exists() and not LOGS_DIR.exists():
            shutil.move(str(old_dir / "logs"), str(LOGS_DIR))
        if (old_dir / "config").exists() and not CONFIG_DIR.exists():
            shutil.move(str(old_dir / "config"), str(CONFIG_DIR))
        try:
            old_dir.rmdir() # Nettoyage si vide
        except OSError:
            pass

# Auto-initialisation au chargement du module
init_app_dirs()
