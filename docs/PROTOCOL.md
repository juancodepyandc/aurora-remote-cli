# Contrats de présentation et de transport

AuroraIA reste en Python. Les commandes, l'authentification, la conversation persistée et le cycle de mission restent dans le client Python. Rust/Ratatui dessine le terminal et collecte le clavier. Il n'interprète aucun shell utilisateur.

## Présentation locale : JSONL version 1

Le lanceur appelle `aurora-native --backend <Python du CLI>`, sans shell. Le renderer lance `Python -m aurora_cli.render_server`. Sous POSIX, le lanceur est remplacé par Rust : deux processus, Rust et le backend Python. Sous Windows, le lanceur attend le renderer.

Chaque message est un objet JSON UTF-8 sur une ligne, suivi de LF. Les retours à la ligne du texte sont échappés dans JSON. stdout du backend est réservé au protocole ; ses autres sorties sont redirigées vers stderr. Le renderer ne montre pas ces logs.

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

Commandes acceptées : `submit`, `theme_preview|theme_commit|theme_cancel`, `model|model_next`, `page|view|view_next`, `picker_cancel|model_cancel`, `doctor`, `stop`, `new`, `quit`. Une version/enveloppe/commande inconnue produit une erreur, jamais une action supposée. Les sélecteurs ne persistent qu'à validation. Une mission active interdit changement de modèle/nouvelle conversation/sortie normale ; Ctrl+C demande l'arrêt.

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
