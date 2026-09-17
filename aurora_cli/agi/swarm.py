import logging
from typing import List, Dict, Callable
from aurora_cli.agi.bus import nexus_bus

logger = logging.getLogger("AGI.Swarm")

class Agent:
    """Entité autonome spécialisée du Swarm."""
    def __init__(self, name: str, role: str, tools: List[Callable]):
        self.name = name
        self.role = role
        self.tools = {t.__name__: t for t in tools}
        nexus_bus.subscribe(f"task.{self.name}", self._on_task)

    async def _on_task(self, data: Dict):
        prompt = data.get("prompt")
        logger.info(f"[{self.name}] Réception de la tâche : {prompt[:30]}...")
        
        # 1. Raisonnement (ReAct - Thinking)
        plan = await self._reason(prompt)
        
        # 2. Action (Tool use)
        result = await self._act(plan)
        
        # 3. Réponse au superviseur
        await nexus_bus.publish("swarm.response", {"agent": self.name, "result": result})

    async def _reason(self, prompt: str) -> str:
        # Simulation de la boucle de raisonnement interne via LLM
        return f"Plan pour {self.name}: Exécuter les outils pertinents pour '{prompt}'"

    async def _act(self, plan: str) -> str:
        # L'agent exécute les outils selon son plan (ici simulé pour sécurité)
        if self.name == "CyberSec":
            return "Audit Sandbox terminé. 0 faille critique."
        elif self.name == "Coder":
            return "Code refactorisé et optimisé (O(1))."
        return "Tâche accomplie génériquement."

class Supervisor:
    """L'Agent Critique qui orchestre le Swarm."""
    def __init__(self):
        self.agents = {}
        nexus_bus.subscribe("swarm.response", self._on_agent_response)

    def register(self, agent: Agent):
        self.agents[agent.name] = agent

    async def delegate(self, prompt: str):
        logger.info("[SUPERVISOR] Analyse métacognitive de la requête...")
        # L'AGI décide quel agent est le plus apte
        if "sécurité" in prompt or "hack" in prompt:
            target = "CyberSec"
        elif "code" in prompt or "optimise" in prompt:
            target = "Coder"
        else:
            target = list(self.agents.keys())[0] if self.agents else "None"
            
        await nexus_bus.publish(f"task.{target}", {"prompt": prompt})

    async def _on_agent_response(self, data: Dict):
        logger.info(f"[SUPERVISOR] Validation du travail de {data['agent']}. Résultat: {data['result']}")
        await nexus_bus.publish("ui.display", f"Agent {data['agent']} a terminé: {data['result']}")
