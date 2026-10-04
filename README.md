# JOBIA — CLI local et distant

Python ≥ 3.10. Référence du serveur : [document maître Aurora](../AuroraIA/ARCHITECTURE_MAITRE.md#cli). Les moteurs ont leurs propres contraintes ; leur disponibilité sur tous les OS n'est pas garantie.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/jobia --help
```

Sous Windows : `.venv\Scripts\python.exe` et `.venv\Scripts\jobia.exe`. `install.sh` / `install.ps1` utilisent le même installateur Python. `jobia`, `jbia` et `aurora` exposent le même client.

Le lancement sans commande ouvre l'interface interactive dans un terminal, ou affiche le tableau d'accueil dans un pipe. Thèmes : `jobia`, `otter`, `abyss`, `plain`. Options globales : `--theme`, `--color auto|always|never`, `--animation auto|full|reduced|none`.

```bash
jobia --theme jobia --animation reduced preview jobia
jobia connect --server https://ADRESSE_DU_SERVEUR
jobia doctor --remote --json
jobia mission "Inspecte ce projet et prépare un plan"
```

`mission` exécute une tâche sur le serveur ; `run` pilote une boucle locale d'amélioration. Une erreur de mission produit un code de sortie non nul. Les fichiers reçus sont contrôlés par taille et SHA-256. La reprise SSE ne rejoue pas un POST et ne survit pas encore à un redémarrage du bridge.

Configuration privée selon les conventions de l'OS, surcharges `JOBIA_CONFIG_DIR` / `JOBIA_DATA_DIR`, migration des anciennes configurations Aurora sans écrasement. Adresse : argument, `JOBIA_SERVER_URL`, `AURORA_SERVER_URL`, configuration ; clé : `JOBIA_API_KEY`, `AURORA_API_KEY`, configuration. Seule une clé déjà autorisée par le bridge permet la connexion ; ne la publie pas.

`doctor` contrôle le client local ; `doctor --remote` vérifie aussi le daemon et sa capacité à accepter une mission. Un contrôle positif ne prouve pas la qualité d'une génération ou le trajet SSE public. La readiness des moteurs est distincte de leur simple présence sur disque.

Le serveur sait charger les instructions des skills, créer des skills de projet sans écrasement, sauvegarder des rôles et exécuter une ou deux tâches de sous-agents. Les permissions directes sont contrôlées ; les commandes autorisées utilisent encore le shell de l'hôte sans sandbox OS. Aucun comportement AGI ni absence universelle de bugs n'est établi.

Ce README est un export de compatibilité du maître.
