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
    MODE_TO_MODEL = {
        "autonome": "",  # Utilise le modèle suprême par défaut du serveur (Qwen3-Coder 80B)
        "pro": "qwen3-coder-next:q4_K_M",
        "balanced": "qwen3-coder:30b",
        "deep": "deepseek-r1:32b",
        "cyber": "qwen-cyber:latest",
        "fast": "qwen3-vl:8b",
        "base": "qwen3-coder:30b",
    }

    def __init__(self):
        self.mode = "autonome"

    def set_mode(self, mode: str):
        m = (mode or "").lower()
        if m in self.MODE_TO_MODEL:
            self.mode = m
            return True
        return False

    def get_model(self):
        return self.MODE_TO_MODEL.get(self.mode, "")

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
                model = self.get_model()
                kwargs = {"session_id": session_id}
                if model:
                    kwargs["model"] = model
                data = client.mission_start(prompt, **kwargs)
                mission_id = data.get("mission_id")
                if not mission_id:
                    return {"status": "error", "data": data.get("error", "Impossible de démarrer la mission.")}
                
                for event in client.mission_stream(mission_id):
                    evt_type = event.get("type")
                    if evt_type == "token":
                        chunk = event.get("content", "")
                        full_text += chunk
                        if callback:
                            callback(task_id, {"stream": True, "data": chunk, "mission_id": mission_id})
                    elif evt_type == "reconnecting":
                        if callback:
                            callback(task_id, {"reconnecting": True, "mission_id": mission_id,
                                               "attempt": event.get("attempt")})
                    elif evt_type == "file_transfer":
                        from aurora_cli.transfers import receive_file
                        out_path = receive_file(event, client)
                        if callback:
                            callback(task_id, {
                                "file_transfer": True, "filename": event["filename"],
                                "path": str(out_path), "size": out_path.stat().st_size,
                                "mission_id": mission_id,
                            })
                    elif evt_type == "error":
                        return {"status": "error", "data": event.get("error") or event.get("message", "Mission échouée"),
                                "partial_text": full_text, "mission_id": mission_id}
                    elif evt_type == "mission_complete":
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
