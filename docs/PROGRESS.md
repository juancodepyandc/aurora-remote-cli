# Progression — refonte du CLI

Mise à jour : 27 octobre 2026. Branche de travail : `redesign/cli-ui`.

## État actuel

Phase 1 validée localement et en CI au commit `34cfd012e4be0d4e44cc66d82462ab3d4bfa6b6c` : les **9 jobs** du [run GitHub 40524176404](https://github.com/juancodepyandc/aurora-remote-cli/actions/runs/40524176404) sont réussis. Python 3.10/3.13 sur Linux/macOS/Windows : tests, lint, wheel et sdist ; Rust sur les trois OS : tests, fmt, clippy et release. Les PTY Linux/macOS couvrent les quatre profondeurs de couleur et le repli Python. Windows valide le binaire et les tests, pas un PTY interactif.

Phase 2 documentée dans [PLAN.md](PLAN.md) et [INVENTORY.md](INVENTORY.md), avec catalogue et registre AST vérifiables. Aucun item du plan n'est implémenté. La PR #4 peut être fusionnée après confirmation de la CI sur son dernier commit ; seule la branche de cette mission sera supprimée après fusion. Les limites physiques des terminaux et la divergence des copies PC restent explicites.

Dernière vérification en lecture seule, le 27 octobre : HEAD PC inchangés (`4baddbd` serveur, `e4e0e5a` CLI). Les brouillons évoluent pendant la mission : serveur 210 suppressions non indexées, 124 fichiers modifiés, 4 modifiés à la fois dans l'index et le travail, 53 entrées non suivies ; CLI 10 fichiers modifiés et 1 entrée non suivie. Aucun de ces changements n'est intégré ni écrasé. `aurora-live` reste propre.

## État initial enregistré

- Phase 0 terminée : dépôt GitHub cloné isolément ; référence CLI `e4e0e5a`, serveur `4baddbd`.
- Copie PC préservée : changements non commités dans les deux dépôts, aucun changement appliqué à ces copies.
- Baseline : **866 passed, 18 skipped, 5 subtests passed**, Python 3.12/Linux, 13,93 s. Build wheel + sdist réussi.
- Pas de commande lint propre au dépôt. Ruff hérite d'une configuration extérieure : 568 diagnostics ; contrôle isolé des erreurs `E9,F63,F7,F82` : 9 noms de types non importés. Un contrat lint reproductible sera ajouté.
- Deux essais en sandbox interrompus : accès loopback empêchant les serveurs HTTP temporaires. Suite relancée hors sandbox, avec fixtures de configuration isolées, verte.
- Spike comparatif terminé en moins de dix minutes : Ratatui retenu pour le renderer, Python conservé pour le métier. Preuves versionnées dans `benchmarks/renderer/`. Huit thèmes déclaratifs et primitives de rendu implémentés : 90 tests ciblés passent, contrôle lint de correction passe, build wheel/sdist passe. Écran principal, commandes et renderer Rust en cours.

## Lots et critères de sortie

1. Cartographie, audit visuel, notes et baseline (ce commit).
2. Spike comparatif Ratatui/Textual/prompt-toolkit ; décision et preuves.
3. Design system, huit thèmes déclaratifs distincts, dégradation, tests.
4. Écran principal, états observés, thème à chaud, clavier et resize.
5. Toutes les commandes/aides/erreurs/connexion, rendu texte et JSON.
6. Snapshots tous thèmes × 60/80/120/200, réseau coupé/repris, lint/build/tests, critique visuelle et corrections.
7. Phase 2 seulement ensuite : inventaire et notes par module, `PLAN.md` sans implémentation du plan.
8. Vérification GitHub CI, fusion sur main si tous les critères de Phase 1 sont validés ; suppression de la seule branche de mission fusionnée. Aucun autre brouillon/branche utilisateur touché.

## Limites de preuve

Pas d'accès au Mac physique ; Terminal.app, iTerm2 et SSH réels ne sont pas encore testés. Les capacités seront testées par environnements simulés et sorties terminal réelles sur Linux ; ces deux niveaux restent distincts. Aucun moteur IA ni entraînement n'est lancé.

## Lot écran principal — 27 octobre

Écran Python recomposé : contexte distant persistant, activité observée, markdown/code/diffs, six vues, aperçu F2 réversible, resize et ASCII. Les faux scores du HUD et le faux succès de fin de cycle ont été retirés. Le contrat de test historique du HUD est remplacé par la visibilité de l’objectif et des seules actions observées. 291 tests ciblés passent ; la suite d’intégration a passé 1209 tests, avec une assertion de libellé héritée ensuite corrigée. Renderer natif et passerelle présents mais validation de parité encore en cours ; aucun déploiement.

## Lot commandes — 27 octobre

Toutes les aides Click utilisent le rendu commun ; erreurs, connexion, appairage, missions, REPL et export suivent les tokens et la dégradation ASCII. Tests : 325 ciblés réussis, dont toutes les aides aux quatre largeurs, JSON, aperçu non persistant, coupure simulée et absence de double streaming. NO_COLOR est prioritaire en détection automatique ; --color always est une demande explicite supérieure. Les contrôles reçus et les balises Rich sont affichés comme texte sûr.

## Lot renderer natif — 27 octobre

Ratatui raccordé au backend Python par JSONL v1 borné. Saisie Unicode, lignes longues, six vues, arrêt/reprise explicites, thème à chaud, repli Python et recherche du binaire intégrés. Documentation PROTOCOL/CLI et CI Linux/macOS/Windows ajoutées. Cargo test : 14 tests réussis ; fmt et clippy -D warnings réussis ; release 1,1 Mo. Build Python wheel/sdist réussi. PTY réel Linux : quatre profondeurs + Python NO_COLOR réussis, resize 80→60, aperçu annulé, thème persisté et coupure HTTP au curseur 1 sans double POST. Résultats dans docs/validation/terminal. Les images de terminaux physiques restent non validées.

## Lot preuves visuelles — 27 octobre

Validation locale Phase 1 terminée : 1212 tests Python réussis, 18 ignorés (modules optionnels), 5 sous-tests ; 14 tests Rust réussis ; Ruff, fmt, clippy -D warnings, wheel et sdist réussis. 80 captures par renderer, huit thèmes et quatre largeurs ; textes, états et PNG natifs dans docs/snapshots. Non-TTY/JSON, NO_COLOR/ASCII, resize, coupure HTTP et reprise testés. Auto-critique et limites dans DESIGN.md. PR GitHub #4 ouverte en brouillon, CI multiplateforme en cours ; aucune fusion tant que ses résultats ne sont pas vérifiés.

## Correctif de validation CI — 27 octobre

La CI native Linux/Windows et les suites Python macOS/Linux passent ; les builds propres ont révélé setuptools absent avec --no-isolation. Ajout à dev de setuptools et wheel (outils de build, aucune dépendance de production). Un test Windows observait le rendu après 80 ms fixes : attente bornée du texte réellement visible. Ces défauts ne sont pas masqués par une exclusion. États de snapshot stockés en une ligne JSONL par fichier pour réduire la répétition du diff. Nouvelle CI exigée avant fusion.

## Lot audit et plan — 27 octobre

Inventaire : 2 222 sources canoniques, 675 déclarations statiques de routes, 111 rôles AST et 182 fichiers source/configuration/test du CLI refondu. Les deux registres ont les mêmes IDs ; cela ne prouve pas la parité des permissions, outils ou sorties. Tableau de maturité sur six axes par famille, audit de la boucle d'autonomie et 18 lots priorisés avec critères mesurables, risques et dépendances. Aucune génération IA, aucun entraînement ni serveur de production lancé. Les notes hors CLI qualifient la preuve disponible, pas une performance de modèle mesurée.
