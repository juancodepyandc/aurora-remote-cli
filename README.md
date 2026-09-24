# Aurora Remote CLI

Client Python léger pour piloter Aurora via HTTP et événements SSE. Le calcul des modèles reste sur le serveur Aurora.

La référence complète se trouve dans `AuroraIA/ARCHITECTURE_MAITRE.md` : [ouvrir le maître dans une installation à dépôts voisins](../AuroraIA/ARCHITECTURE_MAITRE.md#cli). Lire aussi [AGENTS.md](AGENTS.md) avant une intervention.

## Installation et connexion

Créer un environnement client dédié, puis installer ce paquet. La configuration locale vérifiée utilise Python 3.12 ; le minimum déclaré dans pyproject.toml doit encore être harmonisé avec les versions effectivement testées.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/jobia --help
.venv/bin/jobia connect --server https://ADRESSE_DU_SERVEUR
```

Sous Windows, les exécutables de l’environnement sont dans `.venv/Scripts`. Fournir uniquement une clé déjà autorisée par le bridge ; le client peut la demander sans l’afficher (`--api-key`, variable `AURORA_API_KEY` ou saisie masquée). Ne pas déposer de clé dans la documentation.

`connect` sans `--server` résout l’adresse dans l’ordre : argument explicite, variable `AURORA_SERVER_URL`, configuration enregistrée, puis découverte du tunnel publié. Il valide l’adresse découverte (HTTPS et hôte `trycloudflare.com` uniquement), enregistre l’appareil auprès du bridge et mémorise adresse et clé. Si le tunnel enregistré ne répond plus, il tente de redécouvrir l’adresse actuelle avant d’échouer.

La résolution DNS d’un hôte `trycloudflare.com` peut utiliser un secours DNS-over-HTTPS. Ce secours traite le DNS ; il ne corrige ni un serveur arrêté, ni une clé invalide, ni l’incompatibilité SSE du type de tunnel.

## Configuration

Le client lit `config.json` dans son répertoire de configuration (convention XDG), en migrant l’ancienne `~/.aurora/config.json` lorsqu’elle existe. Clés : `server_url`, `api_key`, `default_permissions` (défaut `AUTONOMOUS`), `default_workspace`, `theme`, `language`. `AURORA_SERVER_URL` et `AURORA_API_KEY` servent de surcharge d’environnement.

## Commandes

`aurora` et `jobia` sont deux entrées du même paquet. Le mode sans sous-commande lance le REPL si la connexion est configurée et le serveur répond.

- `connect` : connexion, enregistrement de l’appareil et ouverture du REPL.
- `status` : état du serveur (bridge, Ollama, ComfyUI, matériel, modèles, agents, skills, MCP, connexions).
- `doctor` : contrôles déclaratifs du serveur.
- `permissions [niveau]` : afficher ou définir le niveau par défaut (`SAFE`, `STANDARD`, `AUTONOMOUS`, `FULL`).
- `run REQUÊTE` : mission autonome en une commande (`--model`, `--server-workspace`).
- `agents list`, `agents disable <nom>` : gestion des agents.
- `mcp list` : serveurs MCP et outils découverts.
- `skills list` : skills chargés.

Le REPL accepte `/help`, `/status`, `/permissions`, `/agents`, `/tools`, `/models`, `/mcp`, `/skills`, `/connections`, `/session`, `/sessions`, `/fresh`, `/clear`, `/stop`, `/mode`, `/exit`. `/mode` choisit le niveau d’effort cognitif et le modèle si le moteur local J.O.B.I.A. est disponible.

## Missions et livraison

`run` démarre une mission, affiche sa progression et poursuit le suivi. Le client reprend le flux SSE avec `Last-Event-ID` après une coupure (reprises bornées, jamais de rejeu d’un POST), refuse un trou de séquence et n’annonce une mission terminée qu’à la réception de `mission_complete`.

Pendant le flux : `step_start`/`step_end`, tokens, `file_diff`, `sudo_request` (mot de passe demandé, transmis puis effacé localement), `file_transfer` (artefact réceptionné et vérifié), `remote_command`/`remote_read_file`/`remote_write_file` (traitement local, résultat renvoyé au serveur), `heartbeat`, `reconnecting` et `error`. Un Ctrl+C ou `/stop` demande l’arrêt au serveur.

La réception d’un artefact est vérifiée : nom sûr, confinement au workspace, taille et SHA-256, reprise par plages (`Range`/`If-Range`), renommage d’un `.part` en fichier final uniquement après vérification.

## Limites connues

Les diagnostics ne constituent pas tous des tests de bout en bout. Le daemon doit être disponible pour les missions ; sans lui, le démarrage d’une mission renvoie une erreur. La reprise SSE survit à certaines coupures client, pas à un redémarrage du bridge. Le transport distant doit prendre en charge SSE ; ne pas supposer qu’un Quick Tunnel satisfait cette condition.

Ce README est générable depuis le maître. Le paquet conserve ce fichier pour ses métadonnées d’installation.
