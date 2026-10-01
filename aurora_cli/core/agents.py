"""What JOBIA can delegate, described by role rather than by model name.

The design rule from the source project: an agent is a *job* ("make me a
model 3D", "write me a web page"), and the model satisfying it is chosen later
from what the machine can actually run. So nothing here names a model, and
nothing here hardcodes a size. A catalogue that pinned ``qwen3-coder:30b``
baked in a machine class the author happened to own, and that number is
meaningless on a phone.

Each agent therefore declares:

* the capability it needs (``3d``, ``image``, ``code``, ...),
* how much working memory it wants, as a floor,
* whether an already-installed model can satisfy it,
* which runtimes can host it.

A concrete model enters the picture only at provisioning time, and only if the
scan did not already find a provider for the capability.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Runtime ids that can serve a role. ``*`` means any local runtime.
LOCAL_RUNTIMES = ("ollama", "llamacpp", "lmstudio", "vllm", "localai", "jan",
                  "gpt4all", "koboldcpp", "mlx", "llamafile", "diffusers")


@dataclass(frozen=True)
class Agent:
    """A capability JOBIA can hand off, independent of any model."""

    id: str
    label: str
    capability: str
    #: Free RAM in GB this job wants before it is worth starting.
    ram_floor_gb: float = 4.0
    #: Whether a model already on disk can satisfy the job without any install.
    satisfiable_existing: bool = True
    #: A finished job leaves artefacts worth keeping; used to decide whether to
    #: offer a cleanup that would destroy the result.
    produces_artefacts: bool = True
    runtimes: tuple[str, ...] = ("*",)
    note: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    #: Words that mean this agent, in the languages a user actually types.
    #: Kept on the agent rather than in a separate table so that adding an
    #: agent to the catalogue also teaches the request parser about it, and the
    #: two can never drift apart.
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def can_run_here(self, machine) -> bool:
        """Whether this machine can host the job at all, unpressured."""
        return machine.total_ram_gb >= self.ram_floor_gb


#: The catalogue. Roles, not models.
AGENTS: tuple[Agent, ...] = (
    Agent(id="code", label="Agent Code", capability="llm", ram_floor_gb=8.0,
          note="Écrit et corrige du code, outille le projet.", tags=("code", "cli"),
          keywords=("code", "coder", "programme", "programmer", "script",
                    "fonction", "bug", "corrige", "corriger", "refactor",
                    "debug", "compile", "erreur", "api", "python", "javascript",
                    "typescript", "rust", "html", "css", "test", "unit test",
                    "automatise", "automatiser", "script", "cli", "code")),
    Agent(id="vision", label="Agent Vision", capability="vision", ram_floor_gb=6.0,
          note="Lit images, captures et schémas pour les décrire ou les trier.",
          tags=("image", "analyse"),
          keywords=("image", "photo", "capture", "ecran", "écran", "screenshot",
                    "schema", "schéma", "diagramme", "plan", "dessine", "decrire",
                    "décrire", "analyse", "analyser", "regarde", "regarder",
                    "lis", "lire", "classer", "trier", "reconnait", "reconnaît",
                    "ocr", "texte dans", "vision", "voir")),
    Agent(id="image", label="Agent Image", capability="image", ram_floor_gb=12.0,
          note="Génère des images à partir d'une description.", tags=("image", "creation"),
          keywords=("personnage", "perso", "dessine", "dessiner", "portrait", "genere une image", "génère une image", "cree une image",
                    "crée une image", "dessine", "dessin", "illustration",
                    "poster", "affiche", "visuel", "picture", "genere",
                    "génère", "creation d'image", "création d'image", "paint",
                    "peinture", "style", "render", "rendu", "concept art",
                    "image de", "faire une image", "create an image",
                    "generate an image", "draw", "illustrate", "imagegen")),
    Agent(id="3d", label="Agent 3D", capability="3d", ram_floor_gb=16.0,
          note="Produit un maillage 3D à partir d'une image ou d'un texte.",
          tags=("3d", "creation"),
          keywords=("3d", "maillage", "mesh", "glb", "gltf", "obj", "stl",
                    "modele 3d", "modèle 3d", "objet 3d", "volume", "sculpt",
                    "forme", "triangulation", "reconstruction", "scan 3d",
                    "asset 3d", "make a 3d", "3d model", "mesh from")),
    Agent(id="3d-texture", label="Agent Texture 3D", capability="3d-texture", ram_floor_gb=24.0,
          note="Applique un matériau PBR sur un maillage existant.",
          tags=("3d", "creation"),
          keywords=("texture", "texturer", "texturing", "pbr", "materiau",
                    "matériau", "material", "retexture", "retexturer",
                    "retexture", "habiller", "peindre le maillage", "vertex paint",
                    "appliquer une texture", "texturer un modele")),
    Agent(id="audio", label="Agent Audio", capability="audio", ram_floor_gb=4.0,
          note="Transcription et compréhension audio ; la synthèse utilise le rôle voix.", tags=("audio",),
          keywords=("audio", "son", "voix", "parole", "speech", "voice",
                    "transcription", "transcrire", "dictaphone", "vocal",
                    "narration", "whisper", "microphone", "song", "musique",
                    "transcribe", "speak", "talk")),
    Agent(id="recherche", label="Agent Recherche", capability="llm", ram_floor_gb=8.0,
          runtimes=("remote",), note="Cherche sur le web et synthétise.",
          tags=("web",),
          keywords=("cherche", "chercher", "recherche", "rechercher", "web",
                    "internet", "google", "actualite", "actualité", "news",
                    "en ligne", "enligne", "site", "url", "lien", "source",
                    "search", "look up", "find online", "browse", "research",
                    "cherche sur", "que penses", "wikipedia", "comparaison de prix",
                    "prix", "meteorologie", "météo", "traduis le site")),
    Agent(id="resume", label="Agent Synthèse", capability="llm", ram_floor_gb=8.0,
          note="Résume et réécrit des documents longs.", tags=("texte",),
          keywords=("resume", "résume", "resumer", "résumer", "synthese",
                    "synthèse", "synthetise", "synthétise", "condense",
                    "condensé", "recit", "récit", "explique", "expliquer",
                    "rephrase", "reformule", "reformuler", "tire les grandes lignes",
                    "summarize", "summary", "condense", "explain", "rephrase",
                    "tl;dr", "tldr", "en resume")),
    Agent(id="traduction", label="Agent Traduction", capability="llm", ram_floor_gb=6.0,
          note="Traduit entre langues en gardant le ton.", tags=("texte",),
          keywords=("traduis", "traduire", "traduction", "traduit", "translate",
                    "translation", "en anglais", "en espagnol", "en allemand",
                    "en italien", "en japonais", "en chinois", "en russe",
                    "sous-titre", "sous-titres", "subtitle", "multilingue",
                    "change la langue", "mettre en")),
    Agent(id="math", label="Agent Mathématiques", capability="llm", ram_floor_gb=8.0,
          note="Raisonnement chiffré, preuves et calcul.", tags=("texte",),
          keywords=("calcule", "calculer", "calcul", "mathematique",
                    "mathématique", "maths", "equation", "équation", "derive",
                    "dérivée", "integrale", "intégrale", "probabilite",
                    "probabilité", "statistique", "statistiques", "algebre",
                    "algèbre", "geometrie", "géométrie", "matrix", "matrice",
                    "demo", "démonstration", "prouve", "prouver", "resoudre",
                    "solve", "equation", "plot", "graphique", "stat",
                    "calculate", "compute", "solve", "derivative", "integral",
                    "probability", "algebra", "geometry", "matrix")),
    Agent(id="donnees", label="Agent Données", capability="llm", ram_floor_gb=8.0,
          note="Nettoie, croise et analyse des tableaux de données.",
          tags=("texte",),
          keywords=("donnees", "données", "data", "csv", "excel", "xlsx",
                    "tableur", "spreadsheet", "pandas", "nettoie les donnees",
                    "nettoie les données", "analyse les donnees",
                    "analyse les données", "statistiques du fichier",
                    "croise", "croiser", "fusionne", "fusionner", "jointure",
                    "nettoyer le csv", "analyse de données", "dataframe",
                    "pivot", "nettoyage de donnees", "nettoyage de données",
                    "explore les donnees", "explore les données", "analyze data",
                    "clean data", "merge dataset")),
    Agent(id="document", label="Agent Documents", capability="llm", ram_floor_gb=8.0,
          note="Lit, compare et réécrit des PDF et documents longs.",
          tags=("texte", "bureautique"),
          keywords=("pdf", "docx", "document", "word", "powerpoint", "pptx",
                    "excel", "slide", "diapositive", "diapositives", "contrat",
                    "contrats", "facture", "factures", "cv", "resume de cv",
                    "lettre", "courrier", "rapport", "rapports", "attestation",
                    "certificat", "formulaire", "remplis le formulaire",
                    "remplir le pdf", "analyse le pdf", "lis le pdf",
                    "compare les documents", "signe", "signature",
                    "read pdf", "fill form", "contract", "invoice", "slides",
                    "presentation", "powerpoint", "readme", "manuel",
                    "notice", "mode d'emploi")),
    Agent(id="planning", label="Agent Planification", capability="llm", ram_floor_gb=8.0,
          runtimes=("remote",), note="Transforme une idée en plan d'action.",
          tags=("texte",),
          keywords=("planifie", "planifier", "plan", "planning", "roadmap",
                    "etapes", "étapes", "todo", "a faire", "à faire",
                    "organise", "organiser", "organise mon projet",
                    "organise mon projet", "decoupe", "découpe", "milestone",
                    "roadmap", "plan de travail", "conduis le projet",
                    "triage", "priorise", "prioriser", "backlog", "sprint",
                    "project plan", "break down", "organize my project",
                    "step by step", "action plan", "prioritize")),
    Agent(id="email", label="Agent Courriel", capability="llm", ram_floor_gb=8.0,
          runtimes=("remote",), note="Rédige, trie et répond aux e-mails.",
          tags=("bureautique", "texte"),
          keywords=("email", "e-mail", "mail", "courriel", "reponds a cet email",
                    "réponds a cet email", "reponds a ce mail", "redige un email",
                    "rédige un email", "reponse a cet email", "boite de reception",
                    "boîte de réception", "spam", "newsletter", "sign off",
                    "answer this email", "draft an email", "write an email",
                    "inbox", "reply to", "follow up email", "relance",
                    "relancer", "remercie", "remercier")),
    Agent(id="chat", label="Agent Conversation", capability="llm", ram_floor_gb=4.0,
          note="Dialogue general, conseil et brainstorming.", tags=("texte",),
          keywords=("conseil", "conseille", "idee", "idée", "idees", "idées",
                    "brainstorm", "brainstorming", "discussion", "discute",
                    "pense avec moi", "aide moi a", "aide-moi a", "avis",
                    "opinion", "queconseille", "que conseiller", "pourquoi",
                    "pourquoi tu", "explique moi pourquoi", "idee de projet",
                    "idée de projet", "name", "nomme", "nomme ca", "creativity",
                    "advice", "suggest", "suggestion", "idea", "help me decide",
                    "brainstorm", "why", "opinion", "what do you think")),
    Agent(id="vision-video", label="Agent Vidéo", capability="vision", ram_floor_gb=12.0,
          note="Analyse des séquences d'images ou de vidéo.", tags=("video", "image"),
          keywords=("video", "vidéo", "sequence", "séquence", "film", "clip",
                    "mouvement", "tracking", "suivi", "frame", "images du film",
                    "analyse cette video", "analyse cette vidéo", "sous-titre la video",
                    "sous-titre la vidéo", "decoupe la video", "découpe la vidéo",
                    "anime", "detecte le mouvement", "detect scene",
                    "analyse video", "analyse vidéo", "video analysis",
                    "cut the video", "scene detect", "motion")),
    Agent(id="document-ocr", label="Agent Lecture de documents", capability="vision",
          ram_floor_gb=8.0, note="Extrait le texte des images et documents scannés.",
          tags=("image", "bureautique"),
          keywords=("ocr", "scanne", "scanner", "scan de", "texte sur l'image",
                    "texte dans l'image", "extraire le texte", "lecture de document",
                    "facture scannee", "facture scannée", "ticket de caisse",
                    "passeport", "carte d'identite", "carte d'identité",
                    "releve", "relevé bancaire", "formulaire rempli",
                    "saisie automatique", "digitise", "numérise", "numerise",
                    "extraire les donnees", "extrait les données", "lu les chiffres",
                    "read text from", "scan document", "extract text from",
                    "digitize", "receipt", "invoice scan", "bank statement")),
    Agent(id="image-edition", label="Agent Retouche image", capability="image",
          ram_floor_gb=12.0, note="Retouche, upscale et supprime un fond.",
          tags=("image", "creation"),
          keywords=("retouche", "retoucher", "enleve le fond", "enlève le fond",
                    "supprime le fond", "supprimer le fond", "fonds transparent",
                    "fond transparent", "upscale", "agrandis l'image", "agrandir l'image",
                    "ameliore la qualite", "améliore la qualité", "resize",
                    "redimensionne", "redimensionner", "corrige la photo",
                    "corriger la photo", "eclaircit", "éclaircit", "noir et blanc",
                    "flou", "nettoyage de photo", "photomontage", "montage photo",
                    "edit image", "remove background", "upscale", "resize image",
                    "enhance", "retouch", "background", "transparent background")),
    Agent(id="video-gen", label="Agent Création vidéo", capability="video",
          ram_floor_gb=24.0, note="Génère une vidéo à partir d'une description.",
          tags=("video", "creation"),
          keywords=("genere une video", "génère une vidéo", "cree une video",
                    "crée une vidéo", "video de", "vidéo de", "animation de",
                    "animer", "fais une video", "fais une vidéo", "short video",
                    "clip video", "text to video", "image to video", "text-to-video",
                    "image-to-video", "genere une animation", "génère une animation",
                    "generate a video", "make a video", "video generation",
                    "motion picture", "b-roll")),
    Agent(id="speech", label="Agent Voix", capability="tts", ram_floor_gb=2.0,
          note="Synthèse et narration ; le clonage nécessite un adaptateur distinct.", tags=("audio",),
          keywords=("parle", "parler", "lit a voix haute", "lis a voix haute",
                    "narre", "narrateur", "voix de", "double la voix",
                    "clone la voix", "synthese vocale", "synthèse vocale",
                    "text to speech", "text-to-speech", "voice clone",
                    "narrate", "speak aloud", "read aloud", "tts", "voiceover")),
    Agent(id="musique", label="Agent Musique", capability="music", ram_floor_gb=6.0,
          note="Génère musique, effets sonores et bruitages.", tags=("audio", "creation"),
          keywords=("musique", "music", "morceau", "chanson", "song", "melodie",
                    "mélodie", "beat", "rythm", "rythme", "basse", "guitar",
                    "guitare", "piano", "bande son", "sound effect", "effet sonore",
                    "bruitage", "ambiance sonore", "score", "genera la musique",
                    "génère la musique", "compose la musique", "compose",
                    "soundtrack", "generate music", "make music", "beat",
                    "sound design", "sfx", "jingle")),
    Agent(id="embedding", label="Agent Indexation", capability="embedding",
          ram_floor_gb=4.0, note="Indexe des documents pour les retrouver par sens.",
          tags=("texte",),
          keywords=("indexe", "indexer", "indexation", "vectorise", "vectoriser",
                    "embed", "embedding", "memorise les documents",
                    "mémorise les documents", "recherche par sens",
                    "recherche sémantique", "semantic search", "base de connaissances",
                    "knowledge base", "rag", "retrieval", "similarity search",
                    "index documents", "vectorize", "embed", "knowledge graph",
                    "indexe les pdf", "sois capable de retrouver")),
    Agent(id="math-code", label="Agent Calcul numérique", capability="code",
          ram_floor_gb=8.0, note="Simulation numérique, scikit et dataframe.",
          tags=("code",),
          keywords=("simule", "simuler", "simulation", "numerique", "numérique",
                    "scientifique", "matplotlib", "numpy", "scipy", "pandas en python",
                    "graphe de donnees", "graphe de données", "tracer le graphique",
                    "tracer", "courbe", "regression", "régression", "interpolation",
                    "optimisation", "simul monte carlo", "monte carlo", "scipy",
                    "numerical simulation", "monte carlo", "plot data",
                    "run simulation", "scientific computing", "dataframe chart")),
    Agent(id="web-build", label="Agent Web", capability="code", ram_floor_gb=8.0,
          note="Construit des pages et applications web.", tags=("code", "web"),
          keywords=("site web", "page web", "application web", "app web", "webapp",
                    "landing page", "page d'atterrissage", "html", "css", "tailwind",
                    "react", "next.js", "vue", "svelte", "front-end", "frontend",
                    "back-end", "backend", "api rest", "formulaire de contact",
                    "responsive", "responsive design", "build a website",
                    "build a web app", "web page", "landing page", "react app",
                    "frontend", "fullstack", "full stack")),
    Agent(id="automatisation", label="Agent Automatisation", capability="code",
          ram_floor_gb=6.0, note="Automatise des tâches répétitives et des fichiers.",
          tags=("code", "automatisation"),
          keywords=("automatise", "automatiser", "automatisation", "script qui",
                    "taches repetees", "tâches répétées", "chaque jour", "chaque matin",
                    "tous les jours", "cron", "planifie une tache", "renomme les fichiers",
                    "organise mes fichiers", "trie mes fichiers", "convertis les fichiers",
                    "convertir les fichiers", "backup", "sauvegarde", "sauvegarde automatique",
                    "envoie un mail", "envoie un email chaque", "automatisation de",
                    "automatize", "batch", "rename files", "sort files",
                    "convert files", "scheduled task", "cron job", "backup",
                    "workflow automatique", "macro", "macro automatique",
                    "raccourci clavier", "hotkey", "always run")),
    Agent(id="classify", label="Agent Classement", capability="vision", ram_floor_gb=8.0,
          note="Range automatiquement des images et des fichiers par catégorie.",
          tags=("image", "automatisation"),
          keywords=("classe", "classer", "range", "ranger", "trie", "trier",
                    "categorie", "catégorie", "organise par", "organise mes photos",
                    "organise mes photos", "classify", "categorize", "sort photos",
                    "group by", "folder par", "dossier par", "album photo",
                    "tri par date", "trie par date", "mots cles", "mots-clés",
                    "keywords", "auto tag", "auto-tag", "etiquette", "étiquette")),
    Agent(id="apprendre", label="Agent Apprentissage", capability="llm", ram_floor_gb=8.0,
          runtimes=("remote",), note="Explique un sujet et adapte le niveau.",
          tags=("texte",),
          keywords=("apprends moi", "apprendre", "apprends", "apprivoise moi",
                    "tutoriel", "cours", "lecon", "leçon", "formation", "exercice",
                    "exercices", "quiz", "qcm", "revise", "réviser", "revision",
                    "révision", "examen", "epreuve", "épreuve", "entraine moi",
                    "entraîne moi", "explique comme si", "pour un debutant",
                    "pour un débutant", "niveau debutant", "niveau debutant",
                    "teach me", "learn", "tutorial", "course", "lesson",
                    "explain like", "for a beginner", "practice", "quiz me",
                    "study", "revision", "exam help")),
    Agent(id="planning-voyage", label="Agent Organisation", capability="llm",
          ram_floor_gb=6.0, runtimes=("remote",),
          note="Itinéraires,budget et logistique.", tags=("texte", "vie"),
          keywords=("voyage", "trip", "itineraire", "itinéraire", "roadtrip",
                    "camping", "hotel", "hôtel", "reservation", "réservation",
                    "budget voyage", "que faire a", "que faire à", "visite",
                    "restaurant", "itineraire de", "week-end", "weekend",
                    "travel", "itinerary", "hotel", "booking", "trip planner",
                    "what to do in", "weekend in", "travel plan", "road trip")),
    Agent(id="sante", label="Agent Santé", capability="llm", ram_floor_gb=6.0,
          runtimes=("remote",),
          note="Relit des comptes rendus et explique des analyses.",
          tags=("texte", "vie"),
          keywords=("sante", "santé", "medecin", "médecin", "symptome",
                    "symptôme", "symptomes", "symptômes", "douleur", "diagnostic",
                    "ordonnance", "analyse sanguine", "bilan sanguin", "rendez-vous medical",
                    "rendez-vous médical", "dose", "posologie", "interaction",
                    "allergie", "health", "doctor", "symptom", "blood test",
                    "prescription", "medication", "diagnosis", "allergy",
                    "explain my results", "medical", "dosage")),
    Agent(id="finance", label="Agent Finance", capability="llm", ram_floor_gb=6.0,
          runtimes=("remote",), note="Analyse un budget et des placements.",
          tags=("texte", "vie"),
          keywords=("budget", "depense", "dépense", "depenses", "dépenses",
                    "economie", "économie", "epargne", "épargne", "investir",
                    "placement", "placements", "impot", "impôt", "impots", "impôts",
                    "taxe", "facture d'electricite", "facture d'électricité",
                    "credit", "crédit", "pret", "prêt", "assurance", "bourse",
                    "action", "actions", "crypto", "finance", "budget",
                    "expense", "expenses", "saving", "savings", "invest",
                    "tax", "loan", "insurance", "stock", "portfolio")),
    Agent(id="legal", label="Agent Juridique", capability="llm", ram_floor_gb=6.0,
          runtimes=("remote",), note="Relit un contrat et signale les points à vérifier.",
          tags=("texte", "vie"),
          keywords=("contrat de travail", "juridique", "juriste", "avocat",
                    "legal", "droit", "juridique", "cgv", "conditions generales",
                    "conditions générales", "litige", "contrat de location",
                    "bail", "rgpd", "gdpr", "confidentialite", "confidentialité",
                    "clause", "risque juridique", "legal", "contract review",
                    "terms", "gdpr", "privacy", "nda", "liability", "compliance",
                    "lawsuit", "rental contract")),
)


def by_capability(capability: str) -> list[Agent]:
    return [a for a in AGENTS if a.capability == capability]


def get(agent_id: str) -> Agent | None:
    return next((a for a in AGENTS if a.id == agent_id), None)


def candidates_for(agent: Agent, machine) -> list[Agent]:
    """Other agents that could cover the same job on a weaker or busier machine.

    Shown as fallbacks, never silently substituted: the user is told which
    agent is being proposed and why, because silently swapping the agent that
    generates a 3D mesh for one that only writes text would waste hours of
    compute and produce the wrong artefact.
    """
    if machine.usable and not machine.under_pressure:
        return []
    return [a for a in AGENTS
            if a.capability == agent.capability
            and a.id != agent.id
            and a.ram_floor_gb <= machine.total_ram_gb]
