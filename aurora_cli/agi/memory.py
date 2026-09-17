import os
import logging
import uuid
from typing import List, Dict
from aurora_cli.core.paths import CHROMA_DB_PATH

logger = logging.getLogger("AGI.Memory")

class VectorMemory:
    """Mémoire Associative (RAG) utilisant ChromaDB."""
    def __init__(self, db_path: str = str(CHROMA_DB_PATH)):
        self.db_path = os.path.expanduser(db_path)
        self.collection_name = "agi_long_term"
        self.client = None
        self.collection = None
        self._init_db()

    def _init_db(self):
        try:
            import chromadb
            self.client = chromadb.PersistentClient(path=self.db_path)
            self.collection = self.client.get_or_create_collection(name="aurora_memory")
            self.is_active = True
        except ImportError:
            # Auto-Installation intelligente
            from rich.console import Console
            c = Console()
            with c.status("[bold cyan]J.O.B.I.A détecte un module manquant. Auto-installation de ChromaDB en tâche de fond...[/bold cyan]"):
                import subprocess, sys
                try:
                    subprocess.run(
                        [sys.executable, "-m", "pip", "install", "chromadb", "--break-system-packages"],
                        capture_output=True, check=True
                    )
                    import chromadb
                    self.client = chromadb.PersistentClient(path=self.db_path)
                    self.collection = self.client.get_or_create_collection(name="aurora_memory")
                    self.is_active = True
                    c.print("[bold green]✔ Module ChromaDB installé et greffé avec succès.[/bold green]")
                except:
                    logger.error("Échec de l'auto-installation de ChromaDB.")
                    self.is_active = False

    def store(self, content: str, metadata: Dict[str, str] = None):
        if not self.collection: return
        doc_id = str(uuid.uuid4())
        self.collection.add(documents=[content], metadatas=[metadata or {}], ids=[doc_id])
        logger.debug(f"[MEMORY] Souvenir encodé : {doc_id}")

    def recall(self, query: str, n_results: int = 3) -> List[str]:
        if not self.collection: return []
        results = self.collection.query(query_texts=[query], n_results=n_results)
        if results and results.get("documents"):
            return results["documents"][0]
        return []
