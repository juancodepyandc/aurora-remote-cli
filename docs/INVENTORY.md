# Inventaire des modules et fonctions déclarées

Référence serveur : `4baddbd12e1c32a641455f0002f37eed8596c5a6`. Lecture du document maître, du catalogue canonique et des dictionnaires AST ; aucun import des moteurs. Les sources et défauts de chaque famille sont évalués dans [PLAN.md](PLAN.md).

## Couverture et limites

Le catalogue canonique couvre **2 222 sources**, dont **2 059 AuroraIA**, **159 CLI avant refonte** et **4 découverte aurora-live**. Il recense **675 déclarations statiques de routes**, qui ne prouvent pas autant de routes actives. Les 182 fichiers source/configuration/test CLI de la branche de refonte sont listés séparément. Les fichiers sont repérables dans [sources.tsv](audit/sources.tsv), [cli-redesign-sources.txt](audit/cli-redesign-sources.txt) et [inventory.json](audit/inventory.json). Poids, secrets, bases, sorties, dépendances installées et brouillons PC sont exclus.

L’exhaustivité est celle des familles et des fonctions déclarées dans la référence Git, pas une certification de chaque moteur ni une lecture sémantique de 760 084 lignes. Le catalogue inclut aussi des sources historiques et vendoriées ; leur présence ne prouve pas un usage courant. Les domaines transversaux absents du registre (authentification, transport, stockage, livraison, déploiement, cloud, extensions, tests et documentation) sont évalués séparément dans PLAN.md.

## Registre réellement lu

**111 identifiants** sont présents côté serveur et côté CLI : **1 orchestrateur, 20 entrées maître et 90 sous-agents**. Aucun identifiant manquant ou surnuméraire entre les deux dictionnaires. Les mentions 21/95 rôles dans le maître sont périmées. `conversation-lead` et `chat-lead` sont deux entrées ; leurs permissions déclarées diffèrent (SAFE / AUTONOMOUS), donc leur traitement en alias doit être explicité. L’égalité des noms ne prouve pas l’égalité des politiques ni le fonctionnement des outils. Preuve reproductible : [roles.json](audit/roles.json), extrait AST sans exécution.

Chaque fonction ci-dessous est une déclaration du registre, dont la disponibilité et les critères restent à tester. Les notes du module parent sont dans le tableau du plan.

| Identifiant | Module | Type / parent | Fonction déclarée |
|---|---|---|---|
| `aurora-orchestrator` | orchestration | orchestrator / — | Coordonnateur suprême : découpe les demandes transverses et délègue exclusivement aux Maîtres de Module. |
| `conversation-lead` | chat | master / aurora-orchestrator | Maître du Module Conversation : pilote le dialogue, l'analyse d'intention, la recherche factuelle et la mémoire de chat. |
| `chat-lead` | chat | master / aurora-orchestrator | Pilote le module conversationnel, délègue aux 5 sous-agents de fonctionnalité Chat et vérifie les sources. |
| `conversation-intent-router` | chat | subagent / conversation-lead | Sous-agent Conversation : classifie l'intention, la complexité et la détection de bascule vers un autre module. |
| `conversation-pipeline-tuner` | chat | subagent / conversation-lead | Sous-agent Conversation : gère la boucle 6 étapes (analyze->plan->draft->verify->refine->deliver) et le streaming SSE. |
| `conversation-web-factchecker` | chat | subagent / conversation-lead | Sous-agent Conversation : recherche web temps réel, extraction de sources, citations et anti-hallucination. |
| `conversation-memory-keeper` | chat | subagent / conversation-lead | Sous-agent Conversation : gère l'historique des messages, la compression de fenêtre et les stores de chat. |
| `conversation-vision-multimodal` | chat | subagent / conversation-lead | Sous-agent Conversation : analyse les images, captures d'écran et documents joints dans le chat via Qwen3-VL. |
| `code-lead` | code | master / aurora-orchestrator | Maître du Module Code : coordonne l'architecture, le Motion Design UI, la performance Backend, l'audit et la sandbox. |
| `code-architecture-planner` | code | subagent / code-lead | Sous-agent Code : conçoit l'arborescence multi-fichiers, le graphe d'imports et le préflight des dépendances. |
| `code-design-architect` | code | subagent / code-lead | Sous-agent Code : garantit le Motion Design physique (cubic-bezier, stagger, spotlight, rAF Lerp), le design UI et Three.js. |
| `code-backend-performance` | code | subagent / code-lead | Sous-agent Code : optimise l'architecture Backend, les API, les requêtes SQL/async, la concurrence et la complexité algorithmique. |
| `code-fidelity-auditor` | code | subagent / code-lead | Sous-agent Code : audite la fidélité au brief, bloque tout TODO/placeholder/stub et vérifie les invariants avec SymPy/Z3. |
| `code-sandbox-runner` | code | subagent / code-lead | Sous-agent Code : exécute le code dans la sandbox 22 langages, compile (tsc/py_compile/cargo) et pilote l'auto-réparation. |
| `code-delivery-packager` | code | subagent / code-lead | Sous-agent Code : assemble le livrable final, génère le README, exporte le ZIP et ouvre le projet dans l'éditeur local. |
| `image-lead` | image | master / aurora-orchestrator | Maître du Module Image : coordonne l'expansion de prompt, FLUX.2-dev, l'édition de référence, le 8K/16K PBR et le rangement. |
| `image-prompt-director` | image | subagent / image-lead | Sous-agent Image : développe intelligemment les prompts humains courts/vagues et préserve à 100 % les prompts détaillés. |
| `image-flux-stylist` | image | subagent / image-lead | Sous-agent Image : construit et exécute les graphes ComfyUI FLUX.2-dev (flux2-dev-Q4_K_M.gguf + mistral_3_small_flux2_fp8). |
| `image-reference-researcher` | image | subagent / image-lead | Sous-agent Image : gère la reproduction et la modification fidèle de photos/personnages via ReferenceLatent et recherche visuelle. |
| `image-upscale-16k-pbr` | image | subagent / image-lead | Sous-agent Image : pilote la super-résolution 2×/4K/8K/16K Ultra-Master (16384×16384) et la synthèse Normal/Roughness/AO. |
| `image-job-cancellation` | image | subagent / image-lead | Sous-agent Image : assure l'affichage temps réel de l'étape exacte en cours, l'inscription dans Projets/File et l'arrêt immédiat. |
| `image-output-organizer` | image | subagent / image-lead | Sous-agent Image : maintient l'organisation stricte de `application/output/image/` en 2 seuls dossiers (`creations/` et `modifications/`). |
| `drawing-lead` | drawing | master / aurora-orchestrator | Maître du Module Dessin & Character Forge : coordonne la toile de croquis, l'interprétation du trait, le rendu Sketch-to-Image et la Forge. |
| `drawing-canvas-stroke` | drawing | subagent / drawing-lead | Sous-agent Dessin : gère le moteur de toile HTML5/WebGL, les pinceaux, la pression, les calques et l'historique undo/redo. |
| `drawing-sketch-interpreter` | drawing | subagent / drawing-lead | Sous-agent Dessin : analyse l'intention motrice, la composition, l'anatomie et les relations d'un croquis via Qwen3-VL. |
| `drawing-controlnet-renderer` | drawing | subagent / drawing-lead | Sous-agent Dessin : transforme un croquis en illustration encrée/colorisée fidèle aux lignes maîtresses de l'esquisse. |
| `drawing-character-forge` | drawing | subagent / drawing-lead | Sous-agent Dessin/Forge : orchestre la création de personnages multi-vues, la file `forgeQueueStore` et le handoff vers 3D/Vidéo. |
| `3d-lead` | 3d | master / aurora-orchestrator | Maître du Module 3D : coordonne les 10 sous-agents 3D (routage, génération TRELLIS.2, topologie/Taubin, PBR 16K, rescue, rigging, CAO, assemblage, splats et viewers). |
| `3d-pipeline-router` | 3d | subagent / 3d-lead | Sous-agent 3D : décide du parcours (création TRELLIS.2, modification statique/posturale d'un GLB existant, multi-entités ou CAO). |
| `3d-neural-generator` | 3d | subagent / 3d-lead | Sous-agent 3D : exécute la génération neurale `microsoft/TRELLIS.2-4B` / `Hunyuan3D`, l'adaptateur `modele/renforce/3d/` et l'orchestration multi-entités. |
| `3d-mesh-postprocessor` | 3d | subagent / 3d-lead | Sous-agent 3D : soude les îlots UV sans perte (`weld_fragmented_mesh.py`), applique le filtre Taubin λ/μ à arêtes verrouillées et nettoie la topologie. |
| `3d-texture-lead` | 3d-texture | subagent / 3d-lead | Sous-agent 3D : génère et projette les textures PBR 8K/16K (Albedo, Normal Scharr+Sobel, Roughness, AO Cavity), `COLOR_0` et `face_refine.py`. |
| `3d-quality-rescuer` | 3d | subagent / 3d-lead | Sous-agent 3D : pilote `perfection_gate.py`, `mesh_quality_score.py` (5 axes) et la chaîne de sauvetage autonome (`auto_rescue_mesh.py`). |
| `3d-motion-rigger` | 3d-motion | subagent / 3d-lead | Sous-agent 3D : pilote le rigging Blender 5.1 (`rig_skin_binding.py`, `rigify_autorig.py`), l'animation déclarative (`semantic_animation.py`) et la parité `motion_parser`. |
| `3d-motion-lead` | 3d-motion | subagent / 3d-lead | Pilote le rigging squelettique, le skinning et l'animation sémantique 3D en coordination avec `3d-motion-rigger`. |
| `3d-cad-engineering` | 3d | subagent / 3d-lead | Sous-agent 3D CAO : génère les pièces paramétriques imprimables (STL), la découpe par couleur/plateau, les fixations et la nomenclature (BOM). |
| `3d-assembly-kinematics` | 3d | subagent / 3d-lead | Sous-agent 3D Assemblage : calcule la métrologie (`assembly_precision`), les interférences booléennes Manifold64 et le contrôle continu d'insertion. |
| `3d-splat-telemetry` | 3d | subagent / 3d-lead | Sous-agent 3D Temps Réel : pilote le rendu de prévisualisation par Splats Gaussiens 3D (`HolographicSplatLoader.tsx`) depuis les STL réels. |
| `3d-viewer-lead` | 3d-viewer | subagent / 3d-lead | Sous-agent 3D Viewer : garantit l'affichage dans `ModelView.tsx`, `project_viewer.html` et `engineering_viewer.html` (éclaté 3D, coupe X/Y/Z, optique PBR). |
| `video-lead` | video | master / aurora-orchestrator | Maître du Module Vidéo : coordonne le storyboard cinématographique, la synthèse Wan2.2 5B, HY-Motion et le lip-sync vidéo. |
| `video-storyboard-director` | video | subagent / video-lead | Sous-agent Vidéo : décompose le brief en plans numérotés, valeurs de cadre, mouvements de caméra, raccords et rythme. |
| `video-motion-director` | video | subagent / video-lead | Sous-agent Vidéo : pilote `Wan-AI/Wan2.2-TI2V-5B`, les presets de mouvement caméra et la cohérence temporelle inter-trames. |
| `video-hymotion-kinematics` | video | subagent / video-lead | Sous-agent Vidéo/Animation : pilote `tencent/HY-Motion-1.0` pour la synthèse de mouvements squelettiques 3D/temporels. |
| `video-talking-head` | video | subagent / video-lead | Sous-agent Vidéo : anime les visages parlants (`MuseTalk` / `SadTalker`) et multiplexe la piste audio Kokoro avec le flux MP4. |
| `voice-lead` | voice | master / aurora-orchestrator | Maître du Module Voix : coordonne la transcription Whisper STT, la synthèse Kokoro TTS, le lip-sync Rhubarb et le mode duplex Live. |
| `voice-stt-transcriber` | voice | subagent / voice-lead | Sous-agent Voix : gère la capture micro, la détection d'activité vocale (VAD) et la transcription `faster-whisper-large-v3`. |
| `voice-tts-stt-tuner` | voice | subagent / voice-lead | Sous-agent Voix : pilote la synthèse `hexgrad/Kokoro-82M`, la phonétique française (liaisons, homophones) et le contrôle ASR Whisper. |
| `voice-lipsync` | voice | subagent / voice-lead | Sous-agent Voix : extrait les visèmes temporels (A-X) via Rhubarb et pilote l'animation labiale des avatars UI/3D. |
| `voice-live-duplex` | voice | subagent / voice-lead | Sous-agent Voix : gère la boucle vocale interactive `useVoiceLive.ts`, le push-to-talk, le barge-in et la compatibilité HTTPS/tunnel. |
| `learning-lead` | learning | master / aurora-orchestrator | Maître du Module Academy : coordonne l'oracle formel SymPy/Z3, le corpus académique/Bac, la vérification de quiz et les labs scientifiques. |
| `learning-formal-oracle` | learning | subagent / learning-lead | Sous-agent Academy : vérifie formellement les théorèmes, calculs algébriques et équations via `scientific_oracle.py` (SymPy + Z3). |
| `learning-bac-curator` | learning | subagent / learning-lead | Sous-agent Academy : structure les cours, annales du Bac, fiches méthodologiques et recherches documentaires académiques. |
| `learning-quiz-verifier` | learning | subagent / learning-lead | Sous-agent Academy : génère et vérifie les QCM, flashcards, évaluations sémantiques d'élèves et exports Anki `.apkg`. |
| `learning-lab-integrator` | learning | subagent / learning-lead | Sous-agent Academy : pilote les vues de laboratoires interactifs (`LabPhysics`, `LabChemistry`, `LabAstronomy`, `LabElectronics`). |
| `simulator-lead` | simulator | master / aurora-orchestrator | Maître du Module Simulateur : coordonne la dynamique des corps rigides, les fluides/particules, la chimie et la sérialisation de scènes. |
| `simulator-physics-engine` | simulator | subagent / simulator-lead | Sous-agent Simulateur : implémente la mécanique newtonienne/lagrangienne, les collisions et les contraintes dans `physicsEngine.ts`. |
| `simulator-fluid-particles` | simulator | subagent / simulator-lead | Sous-agent Simulateur : gère la mécanique des fluides (`fluidSolver.ts`) et la dynamique des systèmes de particules (`particleSystem.ts`). |
| `simulator-chemistry-electronics` | simulator | subagent / simulator-lead | Sous-agent Simulateur : modélise les réactions chimiques, la stœchiométrie, l'enthalpie et les lois de circuits. |
| `simulator-scene-io` | simulator | subagent / simulator-lead | Sous-agent Simulateur : gère le chargement/sauvegarde de scènes (`sceneIO.ts`), le store (`sceneStore.ts`) et les presets (`presets.ts`). |
| `cyber-lead` | cyber | master / aurora-orchestrator | Maître du Module Cyber : coordonne l'exécution symbolique Z3/0-day, le patch en boucle fermée, les 9 labs Cyber, PyOps et l'audit réseau. |
| `cyber-symbolic-zeroday` | cyber | subagent / cyber-lead | Sous-agent Cyber : extrait l'AST/CFG et résout les contraintes d'atteignabilité SMT Z3 pour découvrir les 0-days par premiers principes. |
| `cyber-closedloop-patcher` | cyber | subagent / cyber-lead | Sous-agent Cyber : génère les correctifs de sécurité minimaux (`closedLoopPatchEngine.ts`) et re-teste le PoC jusqu'à preuve UNSAT. |
| `cyber-lab-builder` | cyber | subagent / cyber-lead | Sous-agent Cyber : maintient les 9 laboratoires spécialisés (CTF, Crypto, Forensics, Hash, Network, Password, Stego, ThreatIntel, WebSec). |
| `cyber-pyops-keeper` | cyber | subagent / cyber-lead | Sous-agent Cyber : gère les scripts Python `application/python-services/cyber/` et le garde-fou central `_safety.py`. |
| `cyber-network-recon` | cyber | subagent / cyber-lead | Sous-agent Cyber : analyse les configurations réseau IPv4/IPv6, permissions POSIX, règles pare-feu et corrélations CVE/CWE. |
| `cowork-lead` | cowork | master / aurora-orchestrator | Maître du Module Cowork : coordonne l'orchestrateur multi-étapes, l'extension Chrome MV3, l'audit de sécurité d'action et l'extraction documentaire. |
| `cowork-orchestrator-tuner` | cowork | subagent / cowork-lead | Sous-agent Cowork : gère le planificateur (`coworkPlanner.ts`), l'exécuteur (`coworkExecutor.ts`) et le passage de relais inter-agents. |
| `cowork-connector-keeper` | cowork | subagent / cowork-lead | Sous-agent Cowork : maintient `coworkConnectors.ts` et l'extension Chrome Manifest V3 (`application/extension/`, `extension_chrome/`). |
| `cowork-safety-auditor` | cowork | subagent / cowork-lead | Sous-agent Cowork : audite la sécurité des actions automatisées (`coworkSafety.ts`), bloque les actions destructrices non confirmées. |
| `cowork-doc-extractor` | cowork | subagent / cowork-lead | Sous-agent Cowork : ingère et vérifie les fichiers PDF, DOCX, CSV (`inspect_csv`, `csv_json`) et dossiers de travail. |
| `business-ceo` | business | master / aurora-orchestrator | Maître du Module Business (CEO Agent) : coordonne les 9 sous-agents économiques (Marché, Finance, Risque, Entreprise, Quant, Macro, Tech, Critic, Audit). |
| `business-market-research` | business | subagent / business-ceo | Sous-agent Business : gère les flux de marché en direct (BCE, Binance, CoinGecko, Yahoo Finance) et l'analyse de la demande/CAC. |
| `business-financial-analyst` | business | subagent / business-ceo | Sous-agent Business : calcule coûts, marges, BFR (DSO), fiscalité et assure la traduction nette en poche pour le Mode Débutant. |
| `business-risk-manager` | business | subagent / business-ceo | Sous-agent Business : exécute `IndependentRiskManager` (non-LLM), le droit de veto absolu (réserve >= 25%, ruine <= 2%) et l'arrêt d'urgence. |
| `business-entrepreneur` | business | subagent / business-ceo | Sous-agent Business : pilote les entreprises numériques virtuelles (SaaS, B2B, e-commerce) et les actions en direct du Business Lab. |
| `business-quant` | business | subagent / business-ceo | Sous-agent Business : exécute le découpage Walk-Forward (60/20/20), les simulations Monte-Carlo à queues épaisses et l'optimisation bayésienne. |
| `business-economist` | business | subagent / business-ceo | Sous-agent Business : modélise l'inflation, les taux directeurs BCE/Fed, la fiscalité et les stress tests macroéconomiques (2008, 2022). |
| `business-technical` | business | subagent / business-ceo | Sous-agent Business : maintient le client résilient `businessApiFetch`, le Copilote Conversationnel (`/api/business/chat`) et les quotas cloud. |
| `business-critic` | business | subagent / business-ceo | Sous-agent Business : contradicteur systématique (Red-Team économique), débusque biais de survie, retards clients et coûts cachés. |
| `business-auditor` | business | subagent / business-ceo | Sous-agent Business : vérifie la chaîne d'audit SQLite SHA-256, la provenance des cotations et la porte de promotion/rollback des modèles. |
| `training-lead` | training | master / aurora-orchestrator | Maître du Module Entraînement & Modèles Renforcés : coordonne les cycles nocturnes, Kaggle 2×T4, le Flow-Matching DPO 3D, l'audit RTX 5070 Ti et `modele/renforce/`. |
| `training-control-supervisor` | training | subagent / training-lead | Sous-agent Training : gère le démon `auto_rl/control.py`, les verrous exclusifs, l'arrêt immédiat (`stop`) et l'arrêt en fin de cycle (`stop_after_cycle`). |
| `training-kaggle-cloud` | training | subagent / training-lead | Sous-agent Training : orchestre l'envoi des kernels sur Kaggle Double GPU (`gpu_t4_x2` = 30 Go VRAM), vérifie les quotas et rapatrie les poids. |
| `training-3d-flow-dpo` | training | subagent / training-lead | Sous-agent Training 3D : pilote l'algorithme Pareto Flow-Matching DPO + Rectified Velocity Consistency sur `SparseStructureFlowModel` (1.3B) et le pack `TRELLIS-500K`. |
| `training-multi-module-dpo` | training | subagent / training-lead | Sous-agent Training Multi-Modules : entraîne les adaptateurs LoRA/DPO/Surrogate ES pour Image, Code, Cyber, Conversation, Cowork, Academy, Audio, Vidéo et Animation. |
| `training-audit-evaluator` | training | subagent / training-lead | Sous-agent Training Audit : exécute l'audit local sur RTX 5070 Ti (96 maillages 3D / oracles multi-graines), le test BVH de Möller et le juge CLIP 4-vues. |
| `training-reinforced-registry` | training | subagent / training-lead | Sous-agent Training Registre : synchronise `/home/juan/AuroraIA/modele/renforce/<module>/`, génère les fiches `GAINS.md` et pilote le sélecteur UI Base ↔ Renforcé. |
| `missions-lead` | missions | master / aurora-orchestrator | Maître du Module Missions (`agi_core`) : coordonne le journal SQLite/baux, le protocole d'outils structurés, la reprise épistémique et la délégation. |
| `missions-store-lease` | missions | subagent / missions-lead | Sous-agent Missions : gère `agi_core/mission_store.py`, `projectAdmission.ts`, les clés d'idempotence, les baux et la réconciliation des tickets expirés. |
| `missions-protocol-verifier` | missions | subagent / missions-lead | Sous-agent Missions : maintient `mission_protocol.py`, `mission_tools.py`, les contrôles `verify` (`file`/`text`/`json`/`csv_json`) et la revue LLM séparée. |
| `missions-epistemic-recovery` | missions | subagent / missions-lead | Sous-agent Missions : pilote les 3 régimes épistémiques (`KNOWN_CANONICAL`, `KNOWN_OPTIMIZABLE`, `UNKNOWN_EXPLORATORY`) et `_recover_stagnation`. |
| `missions-delegation-spawner` | missions | subagent / missions-lead | Sous-agent Missions : orchestre `spawn_agent` dans `agi_core/mission_agent.py`, le plafonnement de profondeur (`depth <= 2`) et le contrôle parent des workers. |
| `bridge-doctor` | bridge | master / aurora-orchestrator | Maître du Module Bridge & Runtime : coordonne le serveur Flask `:3001`, les blueprints HTTP, le gouverneur GPU/VRAM, le binaire Tauri et systemd. |
| `bridge-routes-contract` | bridge | subagent / bridge-doctor | Sous-agent Bridge : maintient l'enregistrement des 26 blueprints dans `application/routes/`, l'authentification Bearer/Appareil et les règles CORS. |
| `bridge-gpu-governor` | bridge | subagent / bridge-doctor | Sous-agent Bridge GPU : coordonne l'allocation des 16 Go VRAM (RTX 5070 Ti) et 30 Go RAM entre Ollama (`:11434`), ComfyUI (`:8188`) et TRELLIS.2. |
| `bridge-tauri-native` | bridge | subagent / bridge-doctor | Sous-agent Bridge Natif : maintient le cœur Rust Tauri v2 (`application/src-tauri/`), `useTauri.ts`, le fenêtrage Linux/Windows/macOS et `launch_runtime.py`. |
| `bridge-systemd-watchdog` | bridge | subagent / bridge-doctor | Sous-agent Bridge Supervision : vérifie la liveness/readiness de `aurora-bridge`, `aurora-comfyui`, `ollama`, du bus TCP `:3002` et de `start-aurora.sh`. |
| `cli-tunnel-lead` | cli-tunnel | master / aurora-orchestrator | Maître du Module CLI, Tunnel & Mobilité : coordonne JOBIA CLI (`aurora-remote-cli`), le tunnel Cloudflare (`aurora-live`), la livraison d'artefacts et l'espace persistant `:3034`. |
| `cli-jobia-tui` | cli-tunnel | subagent / cli-tunnel-lead | Sous-agent CLI : maintient l'interface plein écran `prompt_toolkit` JOBIA 1.3.0 (`cli.py`, `local_mission.py`) et la parité multi-OS Windows/macOS/Linux. |
| `tunnel-validator` | cli-tunnel | subagent / cli-tunnel-lead | Sous-agent Tunnel : valide l'URL Cloudflare active, la publication `aurora-live/tunnel.txt`, le polling JSON cursé (compatibilité Quick Tunnel sans SSE) et la reprise sur HTTP 530. |
| `cli-artifact-delivery` | cli-tunnel | subagent / cli-tunnel-lead | Sous-agent CLI Livraison : gère `.transfer_to_client/<mission_id>/`, `cli_artifacts.py`, `cli_inputs.py`, `projects_api.py` et la commande `export3d`. |
| `web-action-runner` | cli-tunnel | subagent / cli-tunnel-lead | Sous-agent Mobilité & Workspace : pilote l'espace navigateur persistant `:3034` (`persistent-workspace.mjs`), l'appairage mobile sans clé (`device_pairing.py`) et la navigation tactile V4. |
| `memory-lead` | memory | master / aurora-orchestrator | Maître du Module Mémoire : coordonne l'indexation vectorielle ChromaDB et la consolidation épisodique/sémantique inter-sessions. |
| `memory-vector-indexer` | memory | subagent / memory-lead | Sous-agent Mémoire : gère l'indexation vectorielle ChromaDB (`db_vector/`), la déduplication par hash et la fermeture explicite des descripteurs SQLite. |
| `memory-episodic-consolidator` | memory | subagent / memory-lead | Sous-agent Mémoire : consolide les connaissances vérifiées entre sessions tout en les gardant strictement séparées de l'objectif de la mission active. |
| `mcp-lead` | mcp | master / aurora-orchestrator | Maître du Module MCP : coordonne la découverte/sonde des serveurs MCP (JSON-RPC 2.0 stdio/HTTP) et l'exécution bornée des outils MCP. |
| `mcp-stdio-prober` | mcp | subagent / mcp-lead | Sous-agent MCP : découvre `.mcp.json` / `.aurora/mcp_config.json`, effectue le handshake `initialize` + `tools/list` (`probe_mcp_tools`) avec timeout borné. |
| `mcp-tool-executor` | mcp | subagent / mcp-lead | Sous-agent MCP : exécute `call_mcp_tool` / `invoke_mcp_tool`, vérifie les permissions (`STANDARD`/`AUTONOMOUS`) et enregistre la preuve dans la mission. |
| `skills-lead` | skills | master / aurora-orchestrator | Maître du Module Skills : coordonne la création/audit de compétences réutilisables (`.aurora/skills/<nom>/SKILL.md`) et leur injection contextuelle. |
| `skills-curator-creator` | skills | subagent / skills-lead | Sous-agent Skills : crée et valide les fichiers `.aurora/skills/<nom>/SKILL.md` (`create_skill`) sans écraser une définition différente existante. |
| `skills-context-injector` | skills | subagent / skills-lead | Sous-agent Skills : découvre les skills (workspace, parent, utilisateur, `/etc/aurora/skills`), filtre par `triggers` et injecte jusqu'à 24 000 caractères. |
