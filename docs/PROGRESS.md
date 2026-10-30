# Progression — refonte du CLI

Mise à jour : 29 octobre 2026. Branche de finition : `redesign/cli-composition`.

## État actuel

La refonte initiale a été fusionnée par PR #4 (`bf93aa5`), puis GitHub et la copie CLI du PC sont passés à `f893b6f` (1.5.0). Nouvelle vérification demandée après retour utilisateur sur une finition insuffisante. La qualification esthétique « waouh » n'est pas considérée acquise. La copie serveur reste modifiée ; aucune écriture ni exécution de moteur sur celle-ci.

Baseline réellement rejouée sur 1.5.0 : 1213 tests Python réussis, 18 ignorés, 5 sous-tests ; 14 tests Rust réussis. Des tests supplémentaires ont ensuite reproduit les défauts de Ctrl+C dans les sélecteurs, commandes de présentation bloquées en mission, reprise silencieuse au démarrage, sélection automatique du modèle et restitution/perte de brouillons. Les tests historiques verts ne couvraient pas ces usages.

Correctifs validés localement : contrôles actifs sans remplacer la mission, reprise visible et sans envoi automatique, brouillons conservés, diagnostic d'authentification visible, accueil 60×24 raccourci et panneau de mission vide masqué à l'accueil natif. 1216 tests Python réussis, 18 ignorés, 5 sous-tests ; 19 tests Rust ; Ruff/fmt/clippy, build release, wheel et sdist réussis. 176 captures régénérées, dont les huit accueils en 60×24 pour les deux renderers : les quatre commandes d'accueil restent visibles. Le vrai PTY étendu a passé les deux renderers : deux missions explicitement soumises, une demande d'arrêt depuis le sélecteur et reprise au curseur 1 après coupure. Aucun effet simulé n'est attribué au moteur IA. CI multiplateforme exigée sur le commit publié avant fusion.

Contrôle léger du PC : `/api/health` répond HTTP 200/ok, `/api/cli/doctor` sans identité répond 401. La liaison authentifiée du Mac n'est donc pas validée par cette observation ; aucune clé/configuration réelle n'a été lue ou modifiée pour ce contrôle. Pas d'accès au Mac physique. Résultats de la contre-vérification dans `docs/validation/recheck-2026-10-29/`.

## Validation précédente — historique du 27 octobre

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

## Vérification du parcours installé — 27 octobre

La relecture des commandes Mac a révélé que `install.sh` installe une wheel, tandis que la découverte du binaire à côté des sources ne couvre que le checkout éditable. README/CLI et le message d'erreur indiquent désormais `JOBIA_NATIVE_BIN` explicitement ; le lancement documenté utilise `.venv/bin/jobia`, indépendant du PATH de la session. Aucun changement de configuration de connexion.

Wheel reconstruite et extraite dans un répertoire temporaire hors checkout, dépendances de test déjà installées : le scénario PTY complet passe avec le binaire explicitement désigné, resize, aperçu/annulation, saisie, socket coupée, reprise 0→1 et un seul POST. Résultats et méthode dans [validation/installed-wheel](validation/installed-wheel/README.md). Les 4 tests du lanceur, Ruff et build wheel/sdist passent après correction. La dernière CI doit porter sur ce commit final avant fusion.

## Reprise et composition — 29 octobre

PR #5 fusionnée en `a89c91c` après les neuf jobs réussis du run 40790789266 au commit `b9917ab`. Branche corrective supprimée. Nouveau lot : géométrie, typographie et ornement déclaratifs pour huit compositions ; aucun changement du moteur ou des configurations PC. Les tokens sont ajoutés avant la migration des renderers. La validation visuelle reste ouverte.

Lot tokens : 67 tests design system/thèmes réussis ; aucun ajout de dépendance.

Lot composition : les deux renderers consomment les variantes JSON ; la loutre possède une nouvelle silhouette avec museau rond, pattes et queue. Le panneau de mission vide disparaît aussi de l'accueil Python. Les effets natifs restent bornés et limités au dessin. Première validation : 1361 tests Python réussis, 18 ignorés, 5 sous-tests ; 20 tests Rust, lint/clippy/build réussis. Inspection des huit accueils et correction des limites de hauteur, du mot-symbole étroit et des cadres. Validation finale et CI à suivre.

## Lot de preuves finales — compositions

1377 tests Python réussis, 18 ignorés, 5 sous-tests ; 20 tests Rust. Les 16 nouveaux parcours de resize Python couvrent les huit thèmes en couleur et ASCII/NO_COLOR. Build, lint et clippy verts. 240 snapshots texte, 96 accueils dont les quatre actions sont visibles, 72 PNG et deux planches de revue sont enregistrés dans `docs/validation/composition-2026-10-29/`. PTY : quatre profondeurs natives et Python NO_COLOR ; scénarios étendus pour truecolor/Python. PR #6 ouverte ; la CI du dernier commit publié reste exigée avant fusion. Aucun changement des copies PC, aucune implémentation du plan moteur.

## Régression Mac signalée — 30 octobre

La PR #6 a été fusionnée en `812e9f9`. Le retour réel du Mac a ensuite révélé deux défauts que la CI verte ne couvrait pas : variable JOBIA_NATIVE_BIN invalide bloquant auto, et compteur `steps` du statut serveur utilisé comme liste. Sept tests ont reproduit les échecs avant correction ; 78 tests ciblés passent après correction. Le faux bridge PTY transmet désormais le format réel `steps: entier, plan: liste`. L'accueil d'un historique restauré et `/home` conservent les messages sans rejouer de demande ; les erreurs restent prioritaires sur le décor. Branche de réparation : `fix/macos-launch-and-mission-events`. Installation native sans Cargo en cours. Copie CLI PC toujours `f893b6f` propre ; serveur modifié préservé.

Lot distribution 1.5.1 : installateur natif versionné dans la venv, SHA-256 et exécution `--version` vérifiés avant remplacement atomique. Auto retrouve le binaire installé malgré un ancien export invalide. Compilation/publication préparée sur GitHub pour Mac ARM/Intel, Linux x64 et Windows x64 ; aucune dépendance Python ajoutée. La publication n'est pas encore effectuée. Les tests d'installation sont isolés du profil shell réel ; les anciens ajouts PATH vers pytest sur ce poste ont été retirés après sauvegarde privée. Le contrôle du document maître signale que son catalogue CLI est antérieur aux changements publiés ; contrat serveur concerné vérifié directement dans `mission_api.py` et `mission_store.py`, sans modification de ces copies.

Lot validation Mac : 1406 tests Python réussis, 18 ignorés, 5 sous-tests ; les scénarios PTY natif et Python passent avec le statut serveur réel. 256 captures, dont les huit thèmes avec historique restauré à 120×30 dans chaque renderer ; preuves dans `docs/validation/macos-repair-2026-10-30/`. Le workflow de release vérifiera aussi le téléchargement public et le paquet installé hors checkout sur Mac ARM/Intel et Linux/Windows. PR #7 utilisée pour validation puis intégration ; statut effectif de fusion et publication dans GitHub, aucune version présumée disponible avant ce résultat.

## Accueil après reprise — 30 octobre

La PR #7 est fusionnée en `8a2d9ff` et 1.5.1 publiée ; les 15 jobs de release 41068406384 sont réussis. La nouvelle capture Mac révèle un cas manquant : historique **et mission conservée**. `show_recovery_notices()` appelait `note()`, qui ferme l'accueil ; le thème otter était bien chargé. Baseline : 1406 tests réussis, 18 ignorés, 5 sous-tests. Six nouveaux cas ont reproduit la disparition avant correction. Les avis de reprise occupent maintenant la zone d'activité sous l'accueil ; détails conservés dans l'historique affiché, aucune mission automatiquement lancée. 51 tests UI/protocole ciblés passent. Le parcours exact install.sh → jobia et ses captures restent à valider dans ce lot. Copies PC inchangées ; catalogue maître antérieur au CLI publié, sources concernées vérifiées directement.

Lot installation et preuve 1.5.2 : le PATH de recherche de Python masquait le PATH du terminal appelant ; nouveau test échoué avant correction, puis réussi. Les avis suivent désormais l'état courant, sans rappel périmé après résolution. 1413 tests Python réussis, 18 ignorés, 5 sous-tests ; 21 Rust ; build, Ruff, fmt et clippy verts. 80 captures huit thèmes × deux renderers, 60 à 200 colonnes, dont 200×60 et ASCII/NO_COLOR. PTY avec vraie archive restaurée, zéro POST au démarrage, streaming/coupure et arrêt explicites réussis. Preuves dans `validation/startup-2026-10-30/`. CI renforcée pour exécuter ./install.sh et résoudre jobia dans le shell appelant ; la release vérifiera aussi jobia sans argument hors checkout après téléchargement. Ces étapes GitHub restent à confirmer avant livraison.
