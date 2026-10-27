# JOBIA — CLI local et distant

Python ≥ 3.10. Référence du serveur : [document maître Aurora](../AuroraIA/ARCHITECTURE_MAITRE.md#cli). Les moteurs ont leurs propres contraintes ; leur disponibilité sur tous les OS n'est pas garantie.

```bash
git fetch origin
git switch main
git pull --ff-only
./install.sh
jobia
```

Sous Windows : `.venv\Scripts\python.exe` et `.venv\Scripts\jobia.exe`. `install.sh` / `install.ps1` utilisent le même installateur Python. `jobia`, `jbia` et `aurora` exposent le même client.

Interface Aurora : `jobia` ou `jobia ui` ouvre l'interface plein écran ; un pipe affiche le tableau d'accueil. Conversation, plan, critères, preuves, fichiers reçus et journal suivent les événements réels. F2 thème, F3 modèle, F4 vue, F5 diagnostic, Ctrl+C arrêt, Ctrl+Q sortie. `jobia ui --text` conserve le parcours linéaire. Huit thèmes déclaratifs : `otter` (défaut), `jobia`, `abyss`, `plain`, `phosphor`, `brutal`, `glacier`, `synthwave`. Options globales : `--theme`, `--color auto|always|never`, `--animation auto|full|reduced|none`.

Le panneau « Activité observée » reste visible sur les petits terminaux : étapes horodatées à réception, outils, preuves et fichiers. La sortie reçue s'affiche pendant l'exécution, sans être présentée comme un résultat confirmé. Après 15 secondes sans avancée, une alerte distingue une connexion vivante d'une absence de nouvelles ; un heartbeat ne prouve pas que le moteur travaille. Le texte et le journal sont rafraîchis par lots, avec un aperçu limité aux 16 000 derniers caractères.

```bash
jobia --theme jobia --animation reduced preview jobia
jobia connect --server https://ADRESSE_DU_SERVEUR
jobia doctor --remote --json
jobia mission "Inspecte ce projet et prépare un plan"
```

`mission` exécute une tâche sur le serveur (ou en local via `--host local` / repli avant acceptation avec `find_files`, `search_code`, `git_status`, `list_skills`, `read_skill`, `revise_plan`) ; `run` pilote une boucle locale d'amélioration. Une erreur de mission produit un code de sortie non nul. Les fichiers reçus sont contrôlés par taille et SHA-256. Le journal du serveur survit au redémarrage du bridge. `jobia missions list`, `jobia missions status ID`, `jobia missions stop ID`, `jobia missions local`, `jobia missions watch ID` et `jobia missions resume ID [--model ...]` permettent de retrouver, inspecter, arrêter, suivre et reprendre le même objectif. `jobia agents list|enable|disable|inspect|create|delete`, `jobia mcp list|tools|call` et `jobia skills list|read|create|discover` pilotent le registre d'agents, les outils MCP et les skills du bridge. Dans l'interface : `/attach ID`, `/resume ID`, `/retry` pour une acceptation incertaine avec la même clé d'idempotence. Une action de résultat inconnu doit être inspectée avant rejeu.

`/clear` remet l'interface à zéro et efface les conversations locales du dossier courant. Depuis le terminal, `jobia clear` fait le même nettoyage et `jobia clear --all` inclut tous les dossiers. Les archives sont déplacées dans une sauvegarde locale dont le chemin est affiché ; les missions distantes, les fichiers produits et la configuration restent conservés. `/new` ouvre simplement une nouvelle conversation en gardant les précédentes. Après un nettoyage depuis un autre terminal, redémarre les interfaces déjà ouvertes.

Configuration privée selon les conventions de l'OS, surcharges `JOBIA_CONFIG_DIR` / `JOBIA_DATA_DIR`, migration des anciennes configurations Aurora sans écrasement. Adresse : argument, `JOBIA_SERVER_URL`, `AURORA_SERVER_URL`, configuration ; clé : `JOBIA_API_KEY`, `AURORA_API_KEY`, configuration. Sans autorisation mémorisée, `jobia connect` affiche un code court à saisir dans la fenêtre du PC. L’autorisation reçue est mémorisée automatiquement, jamais affichée ; aucune clé à obtenir manuellement. Les anciennes clés autorisées restent compatibles et ne doivent pas être publiées.

`doctor` contrôle le client local ; `doctor --remote` vérifie aussi le daemon et sa capacité à accepter une mission. Un contrôle positif ne prouve pas la qualité d'une génération ou le trajet SSE public. La readiness des moteurs est distincte de leur simple présence sur disque.

Le serveur conserve l'objectif original, découvre les scripts des modules, mène des expériences, charge les skills, crée des rôles persistants et exécute des tâches de workers avec concurrence configurable. Après une modification, une fin de mission nécessite des contrôles concrets ; une revue LLM de la couverture complète ces mesures sans les remplacer. Ollama utilise ses options natives ou `AURORA_MODEL_OPTIONS` explicite, et les durées/débits viennent du moteur. Les permissions directes sont contrôlées ; les commandes autorisées utilisent encore le shell de l'hôte sans sandbox OS. Aucun comportement AGI ni absence universelle de bugs n'est établi.

Ce README est un export de compatibilité du maître.

Le renderer Rust/Ratatui optionnel se construit avec `cargo build --release --locked --manifest-path native/Cargo.toml`, puis `jobia ui --renderer native`. Le moteur reste Python, et `jobia ui --renderer python` conserve le repli. Voir [installation et tests](docs/CLI.md), [thèmes](docs/THEMES.md), [design et mesures](docs/DESIGN.md), [protocole](docs/PROTOCOL.md). Ces documents décrivent uniquement la présentation du CLI ; les moteurs restent régis par le document maître.
