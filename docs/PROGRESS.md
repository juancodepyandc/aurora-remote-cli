# Progression — refonte du CLI

Mise à jour : 27 octobre 2026. Branche de travail : `redesign/cli-ui`.

## État enregistré

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
