"""
J.O.B.I.A. CORE (Juan Optimized Base Intelligence Architecture)
Le moteur d'orchestration ultime pour Aurora.
"""
from __future__ import annotations
import os
import sqlite3
import subprocess
import threading
import psutil
from datetime import datetime, timedelta
from typing import Callable, Any, Dict, List
import logging

import logging
from aurora_cli.core.paths import AGI_LOG_PATH

# Configuration silencieuse (Fichier uniquement)
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(str(AGI_LOG_PATH), encoding='utf-8')]
)
logger = logging.getLogger("JOBIA")
logging.getLogger("httpx").setLevel(logging.WARNING)

class DockerSandbox:
    def __init__(self):
        self.kali_installed_temporarily = False

    def setup_kali(self):
        logger.info("Vérification de l'image kalilinux/kali-rolling...")
        res = subprocess.run(["docker", "images", "-q", "kalilinux/kali-rolling"], capture_output=True, text=True)
        if not res.stdout.strip():
            logger.info("Installation temporaire de Kali Linux (Docker pull)...")
            subprocess.run(["docker", "pull", "kalilinux/kali-rolling"], check=True)
            self.kali_installed_temporarily = True

    def execute(self, script_content: str, image: str = "kalilinux/kali-rolling", timeout: int = 60) -> dict:
        if image == "kalilinux/kali-rolling":
            self.setup_kali()
            
        logger.info(f"Création d'une Sandbox [{image}]...")
        cmd = [
            "docker", "run", "--rm", "-i",
            "--network", "bridge",
            "--memory", "1g",
            image, "sh", "-c", script_content
        ]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return {
                "success": result.returncode == 0,
                "stdout": result.stdout.strip(),
                "stderr": result.stderr.strip(),
                "code": result.returncode
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "stdout": "", "stderr": "Timeout expiré.", "code": 124}
        except Exception as e:
            return {"success": False, "stdout": "", "stderr": str(e), "code": -1}

    def teardown(self):
        if self.kali_installed_temporarily:
            logger.info("Nettoyage de fin de session : Suppression de l'image Kali Linux...")
            subprocess.run(["docker", "rmi", "kalilinux/kali-rolling", "-f"], capture_output=True)
            self.kali_installed_temporarily = False

from aurora_cli.core.paths import SQLITE_DB_PATH

class LongTermMemory:
    def __init__(self, db_path: str = str(SQLITE_DB_PATH)):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_chunks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT, topic TEXT, content TEXT, resolution TEXT
                )
            """)
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts 
                USING fts5(topic, content, resolution, content='memory_chunks', content_rowid='id');
            """)

    def store(self, topic: str, content: str, resolution: str):
        ts = datetime.now().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("INSERT INTO memory_chunks (timestamp, topic, content, resolution) VALUES (?, ?, ?, ?)", (ts, topic, content, resolution))
            rowid = cursor.lastrowid
            cursor.execute("INSERT INTO memory_fts (rowid, topic, content, resolution) VALUES (?, ?, ?, ?)", (rowid, topic, content, resolution))
            conn.commit()

    def recall(self, query: str, limit: int = 3) -> List[Dict]:
        import re
        safe_query = re.sub(r'[^\w\s]', ' ', query)
        if not safe_query.strip():
            return []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT topic, content, resolution FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT ?", (safe_query, limit))
            return [dict(row) for row in cursor.fetchall()]

class AsyncTaskManager:
    def __init__(self):
        self.tasks: Dict[str, threading.Thread] = {}
        self.results: Dict[str, Any] = {}

    def run_in_background(self, task_id: str, func: Callable, *args, callback: Callable = None):
        def wrapper():
            try:
                res = func(*args)
                self.results[task_id] = {"status": "success", "data": res}
            except Exception as e:
                self.results[task_id] = {"status": "error", "data": str(e)}
            if callback:
                callback(task_id, self.results[task_id])
                
        thread = threading.Thread(target=wrapper, daemon=True)
        self.tasks[task_id] = thread
        thread.start()
        return task_id

from rich.prompt import Confirm

class KaggleQuotaManager:
    """Gère les limites d'utilisation de Kaggle en interrogeant l'API réelle."""
    def __init__(self):
        self.is_grayed_out = False

    def check_quota(self) -> bool:
        # Fini les compteurs artificiels de 2 heures.
        # On interroge réellement le statut de l'API Kaggle locale.
        try:
            import os
            # On vérifie d'abord si le token existe
            if not os.path.exists(os.path.expanduser("~/.kaggle/kaggle.json")):
                logger.error("[QUOTA] kaggle.json introuvable. Mode FAST grisé définitivement.")
                self.is_grayed_out = True
                return False
                
            from kaggle.api.kaggle_api_extended import KaggleApi
            api = KaggleApi()
            api.authenticate()
            
            # Vérification de l'état du réseau Kaggle et des kernels
            kernels = api.kernels_list(mine=True)
            self.is_grayed_out = False
            return True
        except Exception as e:
            logger.warning(f"[QUOTA] Kaggle indisponible (Quota GPU épuisé ou erreur réseau: {e}).")
            self.is_grayed_out = True
            return False

class HardwareAssessor:
    """Évalue en temps réel la capacité matérielle du client."""
    @staticmethod
    def assess_client_capacity() -> bool:
        try:
            ram = psutil.virtual_memory().total / (1024 ** 3)
            cpu_cores = psutil.cpu_count(logical=False)
            return ram > 16.0 and cpu_cores >= 8
        except:
            return False

class JOBIACore:
    """Juan Optimized Base Intelligence Architecture."""
    def __init__(self):
        self.mode = "autonome"
        self.memory = LongTermMemory()
        self.sandbox = DockerSandbox()
        self.async_manager = AsyncTaskManager()
        self.kaggle_quota = KaggleQuotaManager()
        self.hardware = HardwareAssessor()

    def set_mode(self, mode: str):
        valid_modes = ["autonome", "fast", "base"]
        if mode in valid_modes:
            if mode == "fast" and self.kaggle_quota.is_grayed_out:
                logger.error("[J.O.B.I.A] Impossible de forcer FAST. Mode Kaggle grisé (en cooldown).")
                return False
            self.mode = mode
            return True
        return False

    def route_task(self, prompt: str) -> str:
        prompt_lower = prompt.lower()
        is_cyber = any(kw in prompt_lower for kw in ["nmap", "scan", "hack", "cyber", "kali", "audit"])
        is_media = any(kw in prompt_lower for kw in ["vidéo", "3d", "image", "dessin", "render"])
        
        if self.mode == "fast":
            if not self.kaggle_quota.check_quota():
                self.mode = "base"
                return "local_hardware"
            return "kaggle_cloud"
            
        elif self.mode == "base":
            if is_cyber: return "kali_local_sandbox"
            return "local_hardware"
            
        else: # autonome
            if is_cyber: return "kali_local_sandbox"
            if is_media: 
                if self.kaggle_quota.check_quota():
                    return "kaggle_cloud_heavy"
                
            # Évaluation d'exécution distribuée maximale
            # Désactivé temporairement à la demande de l'utilisateur (Mac M-series RAM != GPU serveur)
            # if self.hardware.assess_client_capacity():
            #     from rich.console import Console
            #     c = Console()
            #     c.print("\n[bold yellow]⚡ J.O.B.I.A. Hardware Engine[/bold yellow]")
            #     c.print("Votre machine cliente dispose d'une puissance supérieure (>16Go RAM, >8 Cores).")
            #     if Confirm.ask("Autoriser l'AGI à exécuter cette charge lourde à 100% sur votre client pour préserver les quotas distants ?"):
            #         return "local_client_execution"
                
            return "local_server_llm_logic"
    def process_request(self, prompt: str, callback: Callable = None):
        route = self.route_task(prompt)
        past_context = self.memory.recall(prompt)
        task_id = f"jobia_{datetime.now().strftime('%H%M%S')}"
        
        def worker_logic():
            if "kali" in route:
                script = "echo 'J.O.B.I.A. Cyber Execution' && nmap --version || echo 'Nmap not found'"
                res = self.sandbox.execute(script, image="kalilinux/kali-rolling")
                self.memory.store(prompt, script, res["stdout"] if res["success"] else res["stderr"])
                return res
            else:
                import time; time.sleep(2)
                res = f"J.O.B.I.A. processed via {route}"
                self.memory.store(prompt, "Standard Action", res)
                return res

        self.async_manager.run_in_background(task_id, worker_logic, callback=callback)
        return task_id, route, past_context

    def shutdown(self):
        self.sandbox.teardown()
