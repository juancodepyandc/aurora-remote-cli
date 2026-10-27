# Notes au fil de la refonte

Ces constats sont des preuves de code ou de tests ciblés, pas une évaluation de générations réelles. Le plan global viendra après validation de la Phase 1.

Le plan issu de ces constats est désormais disponible dans [PLAN.md](PLAN.md), avec ses limites de preuve et sans implémentation des recommandations.

| Domaine | Constat et preuve | Suite |
|---|---|---|
| Synchronisation | HEAD PC = GitHub, mais CLI 8 fichiers modifiés ; AuroraIA nombreuses modifications/suppressions et nouveaux fichiers non suivis | Ne pas publier les brouillons du PC ni modifier deux copies |
| Documentation | `python3 scripts/documentation/doc_master.py --check-code` retourne 0, 2222 sources indexées | Ne couvre pas les brouillons non indexés |
| Protocole | CLI `bridge.py` et serveur `application/mission_api.py` alignés pour start, idempotence, status, resume, stop, SSE/polling à curseur | Conserver ; tests réseau simulés avant/après |
| Brouillons serveur | Ajout local d'un second endpoint `cli_epistemic_evaluate` déjà enregistré dans `mission_api.py` | Risque d'échec Flask à rechargement, non exécuté ; signaler sans toucher au travail concurrent |
| Agents/MCP/skills | Forges et distillation nouvelles uniquement dans les brouillons, avec fichiers non suivis | Hors refonte ; intégrer dans plan avec tests de contrats |
| Secrets et diagnostic | `capabilities.env_overrides` recueille tout `JOBIA_*`, puis `jobia env` l'affiche | Allowlist des variables visuelles ; aucune vraie clé consultée |
| Présentation et sécurité | Contrôles terminal et markup présents dans des sorties non échappées | Assainir à la frontière de rendu, conserver code et JSON exacts |
| UI 3D/entraînement | Scores codés en dur ; `/fin-cycle` utilise chemins PC, ignore erreurs et annonce succès | Remplacer faux états par observation/indisponibilité explicite |
| Autonomie | Transport durable ne signifie pas exactement une fois pour effets shell externes | Préserver état inconnu et reprise explicite |
| Permissions | Chemins de mission linéaire exécutent des commandes client ; shell hôte sans sandbox OS | Auditer en Phase 2, ne pas revendiquer sécurité complète |
| Tests | 866 pass, 18 skip, 5 sous-tests ; moteurs optionnels absents et tests ignorés | Aucun score de qualité IA déduit de ces tests |
| Maintenance | Lint non défini dans dépôt ; import de types manquant, styles dispersés | Définir un contrôle reproductible, documenter dette restante |
| Registre des rôles | Extraction AST : 111 IDs communs serveur/CLI, 1 orchestrateur, 20 maîtres, 90 sous-agents ; comptes 21/95 du maître périmés | Q1 : contrat et compte issus d'une source unique ; aucune suppression d'alias sans analyse |
| Alias et permissions | `conversation-lead` SAFE et `chat-lead` AUTONOMOUS sont deux entrées distinctes | Égalité des noms entre dépôts insuffisante pour conclure à la parité des politiques |
| Reprise et effets | Journal et baux présents ; reçus moteur intégrés en 3D, généralisation aux autres moteurs non établie | S3 : injections de crash, état inconnu et absence de double effet |
| Preuves d'autonomie | Hash, structure et revue d'un modèle ne prouvent pas seuls la conformité au besoin | Q2/Q5 : corpus indépendant et verdict mission + livrable |
| Mémoire et skills | Chroma/SQLite ; repli mémoire sur 500 expériences récentes, sélection de skills bornée | S6 : provenance, périmètre, invalidation et oubli vérifiables |
| Authentification | Garde Flask par préfixes, exception loopback, jetons appairés révocables | Q3 : inventaire des routes actives et tests négatifs, sans sonder le bridge de production |
| Installation propre | CI a détecté setuptools absent pour le build sans isolation | Corrigé dans les dépendances de développement ; les 6 jobs Python multiplateformes passent |
| Test de thème à chaud | Attente fixe de 80 ms fragile sur Windows | Corrigé : attente bornée de l'état et de sa représentation ; aucune exclusion de test |
| Validation physique | PTY macOS/Linux et tests Windows réussis ; images et terminaux du Mac utilisateur non observés | Ne pas transformer un résultat CI en validation physique de Terminal.app/iTerm2/SSH |
