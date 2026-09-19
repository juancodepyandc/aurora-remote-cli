"""
J.O.B.I.A. Core - Client Side Proxy
Toute l'intelligence, la mémoire et le routage ont été migrés vers le Cerveau (Brain).
Ce module ne fait plus que proxyfier les requêtes vers l'API distante pour respecter 
le principe "un seul chemin de code partagé".
"""
import logging
import threading
from typing import Callable
from uuid import uuid4

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
        task_id = f"jobia_{uuid4().hex}"
        
        if not client:
            if callback:
                callback(task_id, {"status": "error", "data": "Aucun client Aurora fourni au JOBIACore."})
            return task_id, "error", ""
            
        def worker_logic():
            full_text = ""
            try:
                # La décision (fast, cyber, etc) et le contexte (mémoire) sont gérés CÔTÉ SERVEUR via mission_start
                # On passe le 'mode' demandé en permission ou metadonnée si besoin (ici via model ou flags additionnels).
                data = client.mission_start(prompt, session_id=session_id)
                mission_id = data.get("mission_id")
                if not mission_id:
                    return {"status": "error", "data": data.get("error", "Impossible de démarrer la mission.")}
                
                for event in client.mission_stream(mission_id):
                    if event.get("type") == "token":
                        chunk = event.get("content", "")
                        full_text += chunk
                        if callback:
                            callback(task_id, {"stream": True, "data": chunk, "mission_id": mission_id})
                    elif event.get("type") == "reconnecting":
                        if callback:
                            callback(task_id, {"reconnecting": True, "mission_id": mission_id,
                                               "attempt": event.get("attempt")})
                    elif event.get("type") == "error":
                        return {"status": "error", "data": event.get("error") or event.get("message", "Mission échouée"),
                                "partial_text": full_text, "mission_id": mission_id}
                    elif event.get("type") == "mission_complete":
                        return {"status": "stopped" if event.get("stopped") else "success",
                                "data": event.get("result") or full_text, "mission_id": mission_id}
                return {"status": "error", "data": "Flux fermé sans résultat final.",
                        "partial_text": full_text, "mission_id": mission_id}
            except Exception as e:
                return {"status": "error", "data": f"Erreur d'exécution distante : {e}",
                        "partial_text": full_text}

        def wrapper():
            res = worker_logic()
            if callback:
                callback(task_id, res)
        
        threading.Thread(target=wrapper, daemon=True).start()
        
        return task_id, "server_delegated", ""
