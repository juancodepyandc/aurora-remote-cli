# Contrats de présentation et de transport

AuroraIA reste en Python. Les commandes, l'authentification, la conversation persistée et le cycle de mission restent dans le client Python. Rust/Ratatui dessine le terminal et collecte le clavier. Il n'interprète aucun shell utilisateur.

## Présentation locale : JSONL version 1

Le lanceur appelle `aurora-native --backend <Python du CLI>`, sans shell. Le renderer lance `Python -m aurora_cli.render_server`. Sous POSIX, le lanceur est remplacé par Rust : deux processus, Rust et le backend Python. Sous Windows, le lanceur attend le renderer.

Chaque message est un objet JSON UTF-8 sur une ligne, suivi de LF. Les retours à la ligne du texte sont échappés dans JSON. stdout du backend est réservé au protocole ; ses autres sorties sont redirigées vers stderr. Le renderer ne montre pas ces logs.

`state.welcome` décrit l'accueil demandé, même avec historique et mission conservés. `state.recovery_notices` est une liste optionnelle de résumés de reprise ; absente, elle vaut une liste vide. Les textes sont aussi inclus dans `activity`. Deux avis réservent trois lignes d'activité même à faible hauteur pour rester visibles sans remplacer l'accueil. Cette extension additive reste dans JSONL v1 et n'autorise aucune exécution automatique.

```json
{"version":1,"type":"command","command":"submit","value":"Inspecter le projet"}
{"version":1,"type":"state","state":{"title":"Aurora","status":"idle","busy":false,"connection":"non vérifiée","target":"pc.example","session":"demo","mission":"","latency":"n/d","body":"…","theme":{"id":"otter"}}}
{"version":1,"type":"error","message":"Commande inconnue."}
{"version":1,"type":"exit"}
```

Le deuxième exemple est abrégé. `snapshot()` dans `render_server.py` définit le schéma complet :

| Champs | Signification |
|---|---|
| title, target, connection, session, mission | Identité et état observé ; cible expurgée du userinfo, chemin, query et fragment |
| status, phase, busy, activity | Cycle réel et observations ; aucun pourcentage inventé |
| latency, latency_kind | Durée du dernier diagnostic HTTP, comprenant son traitement serveur ; pas une mesure réseau pure |
| page, tabs, body, sidebar, footer, welcome | Documents/vues, texte markdown et parcours d'accueil |
| theme, themes | Identités déclaratives intégrales : palette, cadres, composants, espacements, bannière |
| model, models, commands, picker | Dernier inventaire reçu, catalogue de commandes, sélecteur actif |
| unicode, color_depth, animation, animate | Capacités du terminal parent, transmises explicitement malgré les pipes du backend |
| prompt | Brouillon pour snapshots ; la saisie native est conservée localement pendant les mises à jour |
| health | Cause lisible du dernier diagnostic, affichée sans masquer une erreur de connexion derrière un accueil vide |
| interactive_commands | Commandes de présentation autorisées pendant une mission ; elles ne remplacent jamais sa tâche ni ses permissions |
| draft_restore | Facultatif : revision croissante et text Unicode d'une demande récupérée ; appliqué une seule fois, sans écraser une saisie déjà commencée |

Commandes acceptées : `submit`, `theme_preview|theme_commit|theme_cancel`, `model|model_next`, `page|view|view_next`, `picker_cancel|model_cancel`, `doctor`, `stop`, `new`, `quit`. Une version/enveloppe/commande inconnue produit une erreur, jamais une action supposée. Les sélecteurs ne persistent qu'à validation. Une mission active interdit changement de modèle/nouvelle conversation/sortie normale ; Ctrl+C demande l'arrêt.

Depuis la vérification du 29 octobre, Ctrl+C reste disponible dans les sélecteurs. Les commandes `/theme`, `/help`, `/view`, `/modules`, `/avant-apres`, `/gains`, `/doctor` et `/missions` restent utilisables pendant une mission. Elles conservent sa tâche active. Les champs supplémentaires restent compatibles avec l'enveloppe v1 ; recompiler le renderer pour bénéficier des interactions et de la restitution du brouillon corrigées.

Bornes : commande ≤ 256 Kio ; état natif ≤ 4 Mio ; aperçu de document 64 000 caractères, queue de streaming 16 000, historique de saisie natif 200 entrées ; queues de transport 64 messages. Un message trop grand ferme la liaison sans interpréter ses fragments. Coalescence des états Python à 40 ms et du document à 100 ms ; dessin natif différentiel et bloquant au repos. Les effets de thème durent au plus 800 ms, à 60 Hz en mode complet, 8 Hz en mode réduit.

Les clés de configuration ne figurent pas dans les états. **Ce processus local n'est pas une frontière de sécurité contre un programme hostile du même utilisateur** : l'environnement du lanceur est hérité, et le backend détient les autorisations nécessaires. Les textes destinés au terminal sont filtrés contre les contrôles ANSI/OSC. Le JSON métier et les demandes saisies conservent leurs données exactes.

Une fermeture du backend n'entraîne aucun relancement ni rejeu automatique. La sortie reste disponible avec Ctrl+Q. Une interruption du renderer exige d'inspecter les missions avant de reprendre. Le backend peut être arrêté à la fermeture du client ; l'état durable distant appartient au PC maître.

## Transport distant existant

Contrat HTTP JSON/SSE déjà publié : `bridge.py` côté CLI et `application/mission_api.py` au commit serveur `4baddbd`. Pas de migration WebSocket : aucun besoin de modifier le serveur pour la présentation.

| Opération | Route et garanties observées |
|---|---|
| Diagnostic / inventaire | GET /api/cli/doctor, /api/cli/models |
| Création | POST /api/cli/mission/start : request, workspace, permissions, session_id, model, idempotency_key ; history/references/context_tokens optionnels |
| Suivi | GET /api/cli/mission/{id}/stream, SSE ordonné avec Last-Event-ID |
| Repli tunnel | GET /api/cli/mission/{id}/events?wait=20 : lot fini, curseur strict et terminal booléen |
| Inspection / reprise / arrêt | GET …/status ; POST …/resume et …/stop |
| Liste | GET /api/cli/missions |

Authentification Bearer via le client Python. HTTPS doit être utilisé hors loopback. Les clés d'idempotence et les curseurs sont conservés ; une acceptation incertaine n'est pas convertie en nouvelle mission. Le GET de suivi retente au plus trois fois, à délai borné, depuis le dernier événement complet. Un trou de curseur ou une fin sans événement terminal produit une erreur explicite. Les fichiers entrants sont vérifiés en taille et SHA-256 par le chemin existant.

Le protocole distant actuel **ne possède pas de version de négociation explicite**. Il est conservé tel quel ; une évolution versionnée et ses tests de compatibilité appartiennent au plan Phase 2, pas à cette refonte. Le JSONL local est bien versionné.

Validation : tests de contrats Python et Rust, plus `scripts/validate_terminal.py` : vrai socket loopback interrompu après l'événement 1, reprise au curseur 1, un seul POST, résultat terminal après l'événement 2. Aucun serveur de production ni modèle contacté.

## Statut distant : compteur et plan

`GET /api/cli/mission/<id>/status` retourne `steps` comme entier (nombre d'événements `step_start`) et `plan` comme liste de textes du checkpoint. Le CLI les conserve séparément. Les événements de flux `plan` et `plan_revision` utilisent en revanche `steps` comme liste. Les collections absentes/nulles/malformées ne remplacent pas les détails déjà observés. Ce contrat est vérifié dans les sources `application/mission_api.py::public` et `agi_core/mission_store.py::append` ; aucun changement serveur n'est nécessaire. Les fixtures de statut et les PTY doivent inclure ce compteur, pas seulement une liste `plan`.

### Canal des tokens de commande

Le flux distant de mission peut ajouter `channel: "tool_call"` à un événement `token`. Ce contenu est le JSON structuré destiné à l'exécuteur : le client le conserve dans son journal borné et met à jour l'activité, sans l'ajouter à la réponse conversationnelle. Un token sans ce champ garde le comportement historique. Le résultat final reste transmis par `mission_complete`. Champ additif, aucune modification de la version du protocole natif.
