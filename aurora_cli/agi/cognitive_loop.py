import asyncio
import logging
from aurora_cli.agi.bus import nexus_bus
from aurora_cli.agi.memory import VectorMemory
from aurora_cli.agi.swarm import Agent, Supervisor

logger = logging.getLogger("AGI.Loop")

class CognitiveEngine:
    """Le cerveau global : connecte la mémoire, le bus d'événements et le swarm."""
    def __init__(self):
        self.memory = VectorMemory()
        self.supervisor = Supervisor()
        
        # Création du Conseil d'Administration de l'AGI (Swarm)
        self.supervisor.register(Agent("Coder", "Ingénieur Logiciel Expert", tools=[]))
        self.supervisor.register(Agent("CyberSec", "Auditeur de Sécurité", tools=[]))
        self.supervisor.register(Agent("Architect", "Architecte Cloud", tools=[]))

    async def process(self, prompt: str):
        logger.info(f"--- NOUVELLE BOUCLE COGNITIVE ---")
        
        # 1. RAG : Récupération du contexte vectoriel
        past = self.memory.recall(prompt)
        if past:
            logger.info(f"[COGNITION] Souvenirs liés trouvés: {len(past)}")
            
        # 2. Lancement du Swarm via le Bus
        await self.supervisor.delegate(prompt)
        
        # 3. Consolidation de la mémoire
        self.memory.store(prompt, {"type": "user_prompt", "status": "delegated"})
        
    def run_sync(self, prompt: str):
        """Wrapper synchrone pour compatibilité avec l'existant."""
        asyncio.run(self.process(prompt))
