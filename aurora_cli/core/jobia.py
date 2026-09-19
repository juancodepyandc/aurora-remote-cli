"""
J.O.B.I.A. Core - Client Side Proxy
Toute l'intelligence, la mémoire et le routage ont été migrés vers le Cerveau (Brain).
Ce module ne fait plus que proxyfier les requêtes vers l'API distante pour respecter 
le principe "un seul chemin de code partagé".
"""
import logging
from typing import Callable
from datetime import datetime

logger = logging.getLogger("JOBIA.Client")

class JOBIACore:
    """Proxy léger vers le Cerveau J.O.B.I.A."""
    def __init__(self):
        self.mode = "autonome"

    def set_mode(self, mode: str):
        valid_modes = ["autonome", "fast", "base"]
        if mode in valid_modes:
            self.mode = mode
            return True
        return False

    def process_request(self, prompt: str, callback: Callable = None, client=None, session_id: str = ""):
        task_id = f"jobia_{datetime.now().strftime('%H%M%S')}"
        
        if not client:
            if callback:
                callback(task_id, {"status": "error", "data": "Aucun client Aurora fourni au JOBIACore."})
            return task_id, "error", ""
            
        def worker_logic():
            try:
                # La décision (fast, cyber, etc) et le contexte (mémoire) sont gérés CÔTÉ SERVEUR via mission_start
                # On passe le 'mode' demandé en permission ou metadonnée si besoin (ici via model ou flags additionnels).
                data = client.mission_start(prompt, session_id=session_id)
                mission_id = data.get("mission_id")
                if not mission_id:
                    return "Erreur : Impossible de démarrer la mission côté serveur."
                
                full_text = ""
                for event in client.mission_stream(mission_id):
                    if event.get("type") == "token":
                        chunk = event.get("content", "")
                        full_text += chunk
                        
                return full_text
            except Exception as e:
                return f"Erreur critique lors de l'exécution distante : {e}"

        import threading
        def wrapper():
            res = worker_logic()
            if callback:
                callback(task_id, {"status": "success", "data": res})
        
        threading.Thread(target=wrapper, daemon=True).start()
        
        return task_id, "server_delegated", ""
