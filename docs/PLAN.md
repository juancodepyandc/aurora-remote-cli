# AuroraIA — audit et plan d’excellence

27 octobre 2026. **Plan proposé, aucune recommandation de Phase 2 implémentée.** La refonte visuelle concerne uniquement le CLI. Le moteur, les poids, les services et les brouillons du PC sont préservés.

## Référence et niveau de preuve

Serveur GitHub/HEAD PC : `4baddbd12e1c32a641455f0002f37eed8596c5a6`. CLI de départ : `e4e0e5a`, refonte sur `redesign/cli-ui`, PR [#4](https://github.com/juancodepyandc/aurora-remote-cli/pull/4). Les HEAD correspondent aux références récupérées ; les copies PC comportent des modifications concurrentes non publiées. Elles ne sont ni auditées comme version livrée ni incorporées au redesign.

Source de départ : [document maître à la référence auditée](https://github.com/juancodepyandc/juan-of-bike-ia-linux/blob/4baddbd12e1c32a641455f0002f37eed8596c5a6/ARCHITECTURE_MAITRE.md), catalogue canonique, registre AST et lectures ciblées des contrats. Le contrôle documentaire effectué en Phase 0 passait pour 2 222 sources indexées. Cela ne valide ni des brouillons ni des comportements dynamiques.

L’[inventaire détaillé](INVENTORY.md) couvre toutes les familles fonctionnelles déclarées, **111 rôles**, les **2 222 entrées du catalogue** et les **182 sources/configurations/tests du CLI après refonte**. Il contient aussi la liste de chaque sous-fonction déclarée et les chemins de preuve. Il ne prétend pas que 760 084 lignes ont été examinées sémantiquement ni que toutes les routes sont effectivement actives.

Exécuté pendant cette mission : tests CLI Linux, tests Rust, build/lint, 160 captures texte, vrais PTY et faux bridge avec coupure de socket. La CI ajoute Windows/macOS ; ses résultats exacts et les commits sont suivis dans [PROGRESS.md](PROGRESS.md). Aucun entraînement, benchmark de modèle, génération média, appel au bridge de production ou opération financière exécuté. Les résultats anciens du maître restent historiques.

## Ce qui empêche actuellement une excellence uniforme

1. Les clients Tauri/web/CLI peuvent atteindre des orchestrateurs différents. Partager un nom de modèle ou de module ne garantit pas les mêmes critères, paramètres, sorties ou reprises.
2. Les contrôles structurels et les avis d’un modèle ne suffisent pas à établir la conformité sémantique. Les anciens corpus du maître contiennent des livrables corrects avec mission en échec et des statuts completed avec résultat faux.
3. Les permissions sont présentes, mais le shell autorisé reste celui de l’hôte. Les déclarations de responsabilité des agents ne sont pas une isolation OS.
4. Les reçus atomiques de moteur sont bien intégrés au parcours 3D ; leur généralisation Image/Code/Voix/Forge n’est pas établie.
5. La qualité, le coût et les latences ne disposent pas d’une mesure comparable pour chaque module. Il faut relever les plus faibles sur un corpus partagé avant d’accroître modèles, parallélisme ou autonomie.

## Tableau par module

Notes **sur 5 de maturité vérifiable**, pour prioriser : **Q** qualité/justesse, **P** performance et maîtrise du coût, **A** autonomie, **F** fiabilité/tests, **S** sécurité, **U** UX. 1 = preuve très insuffisante ou lacune majeure ; 2 = partiel ; 3 = contrat utile et contrôles ciblés ; 4 = largement validé dans le périmètre testé ; 5 = démonstration répétée, représentative, sur tous les parcours. **Une note P=2 ne signifie pas “lent” : les p50/p95 pertinents manquent.** Aucune note n’est un benchmark d’intelligence. Hors CLI, confiance limitée à moyenne, faute de réexécution des moteurs.

Les chemins abrégés `services/`, `hooks/`, `views/` sont sous `application/src/` ; `python-services/` sous `application/`. Les sous-agents de chaque ligne sont détaillés dans INVENTORY.md.

| Module / rôle | État et preuve principale | Défauts ou limites à traiter | Q | P | A | F | S | U |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Orchestration intermodules | Registre `application/agent_registry.py`, routes et rôles, orchestrateurs TS et daemon | Plusieurs chemins d’exécution ; responsabilités déclaratives ; parité de résultat non démontrée | 3 | 2 | 3 | 3 | 2 | 3 |
| Conversation / intention / recherche factuelle | `conversationOrchestrator.ts`, `intentRouter.ts`, vérification, cache, streaming | Citations, suivis contradictoires, cache sensible au contexte et taux d’erreur non mesurés ici | 3 | 2 | 3 | 3 | 2 | 3 |
| Vision et pièces jointes | `conversation-vision-multimodal`, services vision/OCR, modèles configurés | Modèle configuré ≠ modèle chargé ; exactitude OCR, relations spatiales et provenance à tester | 2 | 2 | 2 | 2 | 2 | 3 |
| Code / architecture / génération | `codeOrchestrator.ts`, `codePipeline*`, préflight et corrections | CLI généraliste et pipeline TS distincts ; compilation ≠ conformité, robustesse ou design | 3 | 2 | 3 | 3 | 2 | 3 |
| Code / exécution et livraison | `codeSandbox*`, `codeValidation*`, `codeDelivery*` | Isolation et limites à vérifier par runner ; reprise et tests indépendants incomplets entre surfaces | 3 | 2 | 3 | 3 | 2 | 3 |
| Image / références / édition / upscale | `useImageViewLogic.ts`, `image_module_engine.py`, `comfyJobMonitor.ts` | Attente bornée mais contrat de reprise différent ; netteté/contraste ≠ sujet fidèle ; VRAM à profiler | 3 | 2 | 2 | 3 | 2 | 3 |
| Dessin / croquis / explications | `useDrawingViewLogic.ts`, `drawing*` | Validation de structure ≠ vérité pédagogique ; corpus de transformation de croquis manquant | 2 | 2 | 2 | 2 | 2 | 3 |
| Character Forge | `characterForge.ts`, `forgeQueueStore.ts`, admission persistée | Chaîne intermodules, résultat inconnu conservé ; identité et reprise de chaque étape à mesurer | 3 | 2 | 3 | 3 | 2 | 3 |
| 3D / scènes / géométrie neuronale | `aurora_3d_pipeline.py`, `scene_orchestrator.py`, `generation_3d.py` | Reçus et contrôles techniques présents ; fidélité multi-entités, textures et temps GPU non réévalués | 3 | 2 | 3 | 3 | 2 | 3 |
| 3D / textures / rig / mouvement | `3d-texture-lead`, `3d-motion-*`, moteurs de bake/rig | Détails UV fins, déformations, contacts et animation ne se réduisent pas à un GLB valide | 2 | 2 | 2 | 2 | 2 | 3 |
| CAO et fabrication | `engineering_project.py`, `engineering_mesh.py`, pièces, BOM, export | Parois locales, accès outil, tolérances et essai physique distincts de la métrologie numérique | 3 | 2 | 3 | 3 | 2 | 3 |
| Assemblage / cinématique / viewers | `engineering_motion.py`, `project_viewer.html`, `engineering_viewer.html` | Translation continue bornée, concavité/rotation parfois indéterminées ; éclaté ≠ séquence montable | 3 | 2 | 2 | 3 | 2 | 3 |
| Vidéo / cinéma / talking-head | Rôles vidéo, `cinema_bp_routes.py`, moteurs et variantes UI | Vue active non établie par un type/registre ; cohérence temporelle, annulation et audio à mesurer | 2 | 1 | 2 | 2 | 2 | 2 |
| Voix / STT / TTS / duplex | `voice_service.py`, routes voix, `VoiceCopilotView` | Premier son, WER, prononciation, coupure/barged-in et reprise réseau non mesurés ici | 3 | 2 | 2 | 2 | 2 | 3 |
| Academy / quiz / oracle scientifique | `services/learning/`, `scientific_oracle.py`, vérification académique | Relation formelle prouvée ≠ hypothèses correctes ni pédagogie ; provenance et évaluation aveugle nécessaires | 3 | 2 | 3 | 3 | 2 | 3 |
| Simulateur / physique / chimie / scènes | `services/simulator/`, scènes et rôles de domaine | Convergence numérique, unités, conservation et reproductibilité à distinguer du FPS visuel | 2 | 2 | 2 | 2 | 2 | 3 |
| Cyber / laboratoires / patch | `services/cyber/`, oracle AST/SMT, boucle de remédiation | Autorisation de cible à l’exécution ; domaines d’analyse formelle bornés ; preuve de patch indépendante | 3 | 2 | 3 | 3 | 2 | 3 |
| Cowork / documents / recherche / navigateur | `coworkOrchestrator.ts`, routes fichiers/actions/connecteurs | Connecteur déclaré ≠ connecté ; contenu externe non fiable ; effects navigateur et reprise à borner | 3 | 2 | 3 | 2 | 2 | 3 |
| Business / Lab et Live | `application/business_intelligence.py` : simulation, walk-forward, veto de risque | La simulation ne prouve aucune rentabilité ; données hors ligne, fraîcheur et passage Live à contrôler | 2 | 2 | 2 | 3 | 3 | 3 |
| Entraînement / préférences / LoRA | `auto_rl/`, campagnes, corpus, lignage, audits et promotion | Optimiseur repris ≠ état exact garanti ; contamination train/test et promotion multidomaine à verrouiller | 3 | 2 | 3 | 3 | 2 | 3 |
| Calcul cloud / Kaggle / adaptateurs | `kaggle_sync/`, `auto_rl/cloud*`, runtime/serve | Budget, intégrité d’artefact et compatibilité base/adapter distincts ; déploiement non testé ici | 2 | 2 | 3 | 2 | 2 | 2 |
| Missions / planifier-agir-vérifier-corriger | `agi_core/mission_agent.py`, protocol, recovery, predicates | Contrôles de fin utiles ; choix des preuves et hypothèses faillibles ; convergence non universelle | 3 | 3 | 3 | 4 | 2 | 3 |
| Journal / baux / IPC / reprise | `mission_store.py`, `bus.py`, daemon, `operation_receipts.py` | SQLite durable ; bus non durable ; effet shell inconnu non transactionnel ; reçus moteur surtout 3D | 4 | 3 | 3 | 4 | 3 | 3 |
| Mémoire vectorielle et épisodique | `memory.py` : Chroma + SQLite, `conversationMemory.ts` côté UI | Repli backend limité aux 500 dernières expériences ; provenance/isolement/effacement à unifier | 3 | 2 | 2 | 3 | 2 | 3 |
| Agents / délégation | 111 entrées AST présentes dans les deux registres | Documentation 21/95 périmée ; alias Chat et permissions divergents ; noms égaux ≠ contrats égaux | 3 | 2 | 3 | 3 | 2 | 3 |
| MCP / outils externes | `agi_core/context.py` : JSON-RPC stdio, timeout et taille bornés | Résultat d’outil ≠ effet vérifié ; durée de vie/session, capacités et approbation à couvrir | 3 | 2 | 3 | 3 | 2 | 2 |
| Skills / création / injection | `context.py`, découverte projet/user/global, 10 contenus et 24k caractères max | Sélection bornée mais pas preuve de pertinence ; instruction non fiable, version/provenance à tracer | 3 | 3 | 3 | 3 | 2 | 3 |
| Bridge / routes / authentification | Flask, blueprints, garde des préfixes sensibles, appairage | Inventaire statique ≠ couverture auth active ; accès loopback spécial ; tests négatifs de chaque route requis | 3 | 2 | 3 | 3 | 2 | 3 |
| GPU / modèles / admission | `llm_gateway.py`, `runtime_policy.py`, politiques Comfy/TS | Propriétaires et configurations multiples ; contention, charges/quantifications et swap non comparés | 3 | 2 | 3 | 3 | 2 | 3 |
| CLI / design / natif / texte / JSON | Refonte : 8 thèmes, JSONL v1, 1212 tests Python, 14 Rust, PTY et snapshots | macOS physique/images non observés ; Rust facultatif à compiler ; journal natif Markdown volontairement limité | 4 | 4 | 3 | 4 | 3 | 4 |
| CLI ↔ PC / sessions / SSE / polling | `bridge.py`, `mission_api.py`, idempotence, curseurs, retries bornés | Protocole distant sans version négociée ; diagnostic RTT ≠ latence d’inférence ; plein WAN réel non rejoué | 4 | 3 | 3 | 4 | 3 | 4 |
| Tunnel / découverte / appairage / workspace PC | `device_pairing.py`, persistent workspace, `aurora-live` | URL disponible ≠ moteur prêt ; chemin proxy/auth/cookies et identité du PC à tester bout en bout | 3 | 2 | 3 | 3 | 3 | 3 |
| Artefacts / transfert / stockage | `cli_artifacts.py`, `projects_api.py`, tailles/hash/confinement | Hash ≠ qualité ni signature ; rétention, téléchargements interrompus et provenance multi-moteurs à unifier | 4 | 3 | 3 | 4 | 3 | 3 |
| Clients React / Tauri / mobile / extensions | Stores, IPC Rust, vues, extensions MV3, variantes historiques | Versions chargées et état inter-origines distincts ; permissions navigateur larges à justifier | 3 | 2 | 2 | 3 | 2 | 3 |
| Connecteurs Postgres / S3 / IoT / SSH | Blueprints dédiés, appel via bridge/Cowork | Authentification par connecteur, portée des effets et fixtures réelles non démontrées globalement | 2 | 2 | 2 | 2 | 2 | 2 |
| Exploitation / supervision / distribution | Lanceurs, systemd, readiness, empreintes de builds, cloud bridge | Scripts et services peuvent diverger ; environnements, rollback et sauvegarde/restauration à éprouver | 3 | 2 | 3 | 3 | 2 | 3 |
| Tests / documentation / variantes historiques | Catalogue et exports du maître, tests ciblés, audits anciens | Comptes de rôles périmés, version Python 3.9/3.10 discordante ; couverture globale non calculée | 3 | 2 | 2 | 3 | 3 | 3 |

## Autonomie : garanties présentes et limites

| Étape | Ce qui existe dans la référence | Ce qui manque pour augmenter l’autonomie |
|---|---|---|
| Planifier | Objectif original, critères exacts, outils requis bornés par la demande, révision du plan | Mesure de couverture des exigences et faisabilité par domaine ; pas d’effacement d’un critère échoué |
| Agir | Schémas d’outils, permissions, baux, checkpoint avant processus, délégation bornée | Isolation OS, approbations liées à un effet concret, contrats communs à tous les runners |
| Vérifier | Hashes actuels, fichiers/JSON/commandes, prédicats AST sans eval, acceptation des workers, revue finale | Oracles indépendants et conformité sémantique ; une revue du même modèle ne suffit pas |
| Corriger | Signatures de stagnation, diagnostic fondé sur observations, budget de reprises | Taux de réparation hors corpus connu, choix d’expériences discriminantes, coût par succès |
| Reprendre | Journal durable, idempotence, cursor replay, processus interrompus non rejoués automatiquement | Reçus Image/Code/Voix/Forge ; tests de crash à chaque frontière d’effet |
| Mémoriser | Contexte borné, souvenirs Chroma/SQLite, skills et rôles persistés | Provenance, périmètre, dates, invalidation, oubli et mesure de rappel utile |
| S’arrêter | Budgets, arrêt, statuts blocked/failed/stopped, conservation des inconnus | Échéance globale et récupération de ressources pour tous les moteurs ; coût maximal annoncé avant action |

Dans le code lu, `_run_process` contrôle le niveau d’outil mais utilise `create_subprocess_shell` ou `create_subprocess_exec` sur l’hôte. Une permission AUTONOMOUS/FULL ne remplace pas une confirmation d’action destructive. La confirmation doit porter sur la cible, l’effet et la portée réellement exécutés, puis expirer et être invalidée si ces éléments changent. Le renderer n’a pas ajouté une fausse action “Autoriser” : le protocole actuel ne fournit pas ce contrat d’approbation distante.

## Mesures à établir avant toute optimisation lourde

Chaque relevé doit lier commit, runtime/poids/adapter, matériel, entrée, seed si disponible, profil et budget. Séparer **attente**, **chargement**, **premier token/aperçu/son**, **calcul**, **post-traitement**, **livraison**, **vérification**. Comparer froid et chaud à qualité constante.

Baseline IA courante : **non mesurée pendant cette mission**. Ne pas remplir des p50/p95 à partir de deux exemples historiques. Après accord, minimum 30 répétitions pour une mesure de latence technique et ≥20 tâches tenues à l’écart de l’entraînement par famille pour une première mesure de succès ; trois exécutions par tâche pour les chemins stochastiques. Publier distribution, intervalle d’incertitude, nombre d’échantillons, échecs et budget consommé.

Mesures CLI observées ici : premier écran natif Linux 264–315 ms selon profil, ~49 Mio Rust+backend Python, 0 octet au repos sur la fenêtre de 1,5 s ; CPU non discernable à 10 ms de résolution. Ces valeurs ponctuelles ne sont pas des SLO de production. Le microbenchmark de frames se trouve dans [DESIGN.md](DESIGN.md), distinct du client complet.

Tableau de bord futur : succès **mission et livrable indépendamment validé**, faux succès, arrêt/reprise, actions dupliquées, taux d’intervention humaine, réussite de réparation, p50/p95 par étape, RAM/VRAM/swap, temps de démarrage, taux de couverture de branches critiques, couverture de routes protégées, régressions de corpus. Une moyenne globale ne doit pas masquer le pire module.

## Ordre des lots proposé — accord requis

Les nombres ci-dessous sont des **critères à atteindre**, pas des résultats observés. Les objectifs relatifs se comparent à une baseline répétée sur le même matériel. Tout gain échoue si la qualité ou la sécurité se dégrade.

### A. Quick wins — réduire les faux états et rendre les faiblesses mesurables

| Ordre / ID | Objectif et parcours | Acceptation mesurable / métriques | Dépendances | Risques et compromis |
|---|---|---|---|---|
| 1 / Q1 | Aligner catalogue, rôles et versions supportées (tous clients) | 100 % des 111 IDs et alias expliqués ; zéro entrée sans propriétaire/contrat ; version Python unique entre packaging/README/CI ; contrôle documentaire automatique | Référence Git et inventaire | Ne pas supprimer un alias actif ; migration conservatrice des configurations |
| 2 / Q2 | Instrumenter les résultats et établir le corpus indépendant par domaine | ≥20 tâches par famille, seeds/entrées gelées, verdict indépendant mission+livrable, baseline p50/p95 avec ≥30 répétitions techniques ; 100 % des mesures ont une provenance ; zéro prompt/clé en logs par défaut | Q1 | Coût de calcul : séparer tests légers quotidiens et campagnes GPU explicitement budgétées |
| 3 / Q3 | Couvrir les frontières d’accès et confirmations | Inventaire des routes **effectivement enregistrées** ; 100 % des routes à effets testées sans identité, identité révoquée, capacité insuffisante et proxy ; zéro effet dans les tests de refus ; approbation descriptive pour actions destructives | Q1, Q2 ; prérequis de S1 | Ne pas casser appairage existant ; introduire d’abord tests et journaux expurgés |
| 4 / Q4 | Unifier liveness/readiness/capacité réelle et état chargé | Doctor distingue les trois états ; 100 % des moteurs absents/dégradés donnent cause et action ; commit/version runtime visibles PC/web/CLI ; ≤500 ms p95 pour les sondes locales légères | Q1, Q2 | Sondes actives limitées : ne pas charger un modèle lourd pour une simple aide |
| 5 / Q5 | Verrouiller les preuves actuelles et éviter le faux succès | Corpus de contre-exemples : verdict faux rejeté même avec JSON valide/revue positive ; 100 % des résultats lie entrée, outil, version, hash et limites ; aucun critère invalide conservé après changement d’octets | Q2 | Hash et oracles ajoutent du coût ; mesurer p95 de vérification, ne pas la supprimer pour gagner du temps |

### B. Chantiers structurants — remonter les maillons faibles

| Ordre / ID | Objectif et parcours | Acceptation mesurable / métriques | Dépendances | Risques et compromis |
|---|---|---|---|---|
| 6 / S1 | Isoler les outils et centraliser la politique | Sandbox OS ou runner isolé selon plateforme ; 0 sortie du workspace, 0 accès secret et 0 effet réseau non accordé sur suite adversariale ; mêmes refus parent/worker/MCP/TS ; overhead p95 mesuré et cible <10 % sur outils courts | Q3, Q5 | Compatibilité outils/GPU ; exceptions explicites, jamais élargissement silencieux |
| 7 / S2 | Contrat versionné de job/événement/artefact commun | Schémas v1 et adaptation des anciens clients ; négociation de version ; 100 % du corpus de contrats identique natif/web/CLI ; erreurs typées ; aucune divergence d’ID/critère/hash | Q1, Q4, Q5 | Migration incrémentale par adaptateur, pas réécriture simultanée des moteurs |
| 8 / S3 | Reprise durable pour Image, Code, Voix et Forge | Crash avant/après chaque commit d’étape ; zéro action acceptée rejouée ; état inconnu explicite ; résultat fini récupéré avec mêmes hashes ; reconnexion locale p95 <2 s hors backoff et aucun trou de curseur | S2, Q5 | Transactions interservices imparfaites ; distinguer reçu de calcul et preuve sémantique |
| 9 / S4 | Ordonnanceur GPU/RAM et registre effectif des modèles | Réservation unique par étape, file/annulation ; 0 double admission sur stress tests ; corpus mixte sans OOM sous budget admis ; p95 et pic VRAM publiés ; cible −20 % p95 à qualité constante | Q2, Q4, S2 | Équité interactif/batch, risque de sous-utilisation ; déploiement d’abord en mode observation |
| 10 / S5 | Réparation autonome fondée sur expériences | ≥80 % de réparations sur pannes tenues à l’écart et reproductibles ; faux succès 0 sur les contre-exemples bloquants ; tours/coût et p95 bornés ; pas de rejeu d’effet inconnu ; arrêt si pas de progrès | Q2, Q5, S1, S3 | Réparer plus longtemps peut masquer un modèle inadéquat ; budget et témoin sans réparation obligatoires |
| 11 / S6 | Mémoire/skills avec provenance, portée et oubli | Chaque souvenir/skill a source, propriétaire, date, version et statut ; suppression effective testée ; corpus de rappel : précision utile ≥90 %, aucune fuite entre workspaces ; p95 rappel <150 ms sur corpus fixé | Q2, S1, S2 | Compromis rappel/précision ; indexation coûteuse, migration des anciennes expériences conservatrice |
| 12 / S7 | Parité de résultat sur les parcours multimodaux | Même entrée et obligations depuis PC/web/CLI ; corpus par domaine ≥85 % de succès vérifié, aucune famille <80 % ; références, unités, scènes/entités et livraisons identiques ; écarts expliqués | S2–S6 | Ne pas forcer les UX identiques ; conserver besoins propres à voix, dessin, CAO |
| 13 / S8 | Livraison, rétention et restauration éprouvées | 100 % des artefacts ont manifeste/intégrité/provenance ; coupure de téléchargement testée ; restauration de sauvegarde vérifiée sur fixtures ; zéro perte silencieuse lors de migration ; volume/rétention visibles | S2, S3, Q3 | Coût disque/hash ; nettoyage uniquement des données explicitement éligibles |
| 14 / S9 | Distribution et observabilité cohérentes | Build identifié sur chaque surface ; démarrage/arrêt/reprise sans processus orphelin ; installs neuves CI sur trois OS ; CLI p95 à froid <500 ms et repos <0,1 % cœur sur 60 s, paramètres de mesure publiés | Q1, Q2, S2 | Signatures/release/binaire natif par architecture ; éviter de regrouper poids IA avec le client léger |

### C. Paris ambitieux — seulement après franchissement des seuils

| Ordre / ID | Objectif | Acceptation mesurable | Dépendances | Risques |
|---|---|---|---|---|
| 15 / B1 | Planificateur d’expériences choisissant le test le plus informatif | A/B sur tâches inédites : +15 points de succès ou −25 % coût par succès, sans hausse des faux succès ; intervalles d’incertitude publiés | Q2, S5, S6 | Le planificateur peut sur-optimiser le corpus ; évaluateur indépendant et cas scellés |
| 16 / B2 | Génération 3D/CAD fabricable et assemblage autonome | Tous composants couverts, contraintes de volume/paroi/jeu/outil, cinématique et séquence contrôlées ; cas concaves/rotations avec verdict indéterminé honnête ; validation physique sur un lot convenu | S4, S7, S8 | Coût géométrique, précision matérielle ; aucune certification déduite d’un rendu |
| 17 / B3 | Apprentissage continu à promotion contrôlée | Baseline figée et canary ; +5 points minimum sur domaine ciblé, régression ≤1 point sur chaque autre domaine ; zéro cas de contamination détecté ; rollback reproduit | Q2, Q5, S4, S8 | Catastrophic forgetting, biais des juges, coût GPU ; approbation explicite avant promotion de poids |
| 18 / B4 | Agent de longue durée supervisable | Missions ≥24 h sur environnement de test, ≥90 % de reprise contrôlée après pannes, zéro action destructive sans autorisation liée à l’effet ; budget/état/preuves inspectables à tout moment | S1–S9 | Effets externes irréversibles et dérive d’objectif ; maintien d’un arrêt indépendant |

**Règle de progression :** Q1–Q5 d’abord, puis S1–S3 ; S4–S9 par dépendances et faiblesse mesurée. Aucun pari B ne démarre tant qu’un module essentiel demeure sous son seuil ou que la couverture des effets dangereux est incomplète. Pour chaque lot : contre-exemple reproduit, correctif général, tests, campagne budgétée, comparaison au témoin, revue des limites, puis décision de promotion.

## Première décision attendue après lecture

Autoriser ou ajuster **Q1–Q3**, avec un budget de campagne pour Q2, avant tout travail sur les moteurs. Aucun nouveau modèle, entraînement, serveur cloud, nettoyage de données ou changement du PC maître n’est nécessaire pour la présente refonte. Les cibles de latence des moteurs seront fixées par famille après la baseline ; les chiffrer maintenant comme résultats serait inventer.
