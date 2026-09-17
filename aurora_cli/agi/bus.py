import asyncio
import logging
from typing import Callable, Dict, List, Any

logger = logging.getLogger("AGI.Bus")

class EventBus:
    """Système Nerveux Central : Pub/Sub asynchrone pour découpler les modules."""
    def __init__(self):
        self.subscribers: Dict[str, List[Callable]] = {}
        self.history: List[Dict[str, Any]] = []

    def subscribe(self, event_type: str, callback: Callable):
        if event_type not in self.subscribers:
            self.subscribers[event_type] = []
        self.subscribers[event_type].append(callback)
        logger.info(f"[BUS] Souscription enregistrée pour : {event_type}")

    async def publish(self, event_type: str, data: Any):
        self.history.append({"event": event_type, "data": data})
        if event_type in self.subscribers:
            tasks = []
            for callback in self.subscribers[event_type]:
                if asyncio.iscoroutinefunction(callback):
                    tasks.append(asyncio.create_task(callback(data)))
                else:
                    callback(data)
            if tasks:
                await asyncio.gather(*tasks)

# Singleton global pour l'AGI
nexus_bus = EventBus()
