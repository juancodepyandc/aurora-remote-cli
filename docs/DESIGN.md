# Aurora — direction et système terminal

## Périmètre et méthode

Refonte exclusive de `aurora-remote-cli`. AuroraIA reste en Python sur le PC maître. Point de départ GitHub : CLI `e4e0e5a`, serveur `4baddbd`. Une copie de travail isolée évite les changements locaux concurrents. Les exigences de cette mission autorisent ces documents ciblés ; le document maître Aurora reste la référence des moteurs.

## Audit du rendu initial

| Problème observé dans le code | Conséquence |
|---|---|
| `ui.py` HUD sinusoïdal et invalidation toutes les 150 ms même au repos | CPU et trafic terminal sans information nouvelle |
| `activity_text` absent du layout, panneau secondaire masqué sous 96 colonnes | Outils, plan et progression invisibles sur petit terminal ; assertions de test commentées |
| Scores 3D fixes et capacités extrapolées dans le HUD/accueil | Décor présenté comme télémétrie ; faux sentiment de validation |
| En-tête, bandeaux, cadres et aide répétitive occupent la hauteur | Conversation écrasée sur 60×24 |
| Cible dans un footer tronqué, session/latence absentes | Contexte distant difficile à comprendre |
| Rich, Click, console indépendante, stdout et prompt-toolkit disparates | Thèmes, erreurs et ASCII incohérents |
| Largeur Rich fixée au lancement | Redimensionnement incomplet |
| Emojis larges, bannière sans ASCII, bordures Unicode résiduelles | Alignements instables selon terminal |
| Texte brut plein écran ; diffs avec markup non échappé | Markdown peu lisible, code ambigu et numéros de lignes faux |
| Bannière de 1,2 à 2,2 s et aide typewriter | Délai artificiel avant utilisation |
| Horloges wall-clock/monotonic mélangées dans le spinner | Durée incorrecte |

## Directions envisagées

1. **Horizon boréal** : rubans fins émeraude/cyan/violet, conversation éditoriale, rail de contexte stable. Signature immédiatement identifiable, espace utile prioritaire.
2. **Atelier marémoteur** : loutre pixel expressive tenant une coquille, lignes de marée et accents nacrés ; la mascotte accompagne une activité réelle, puis se repose. La plus singulière, retenue comme défaut sous réserve du spike et de l'inspection.
3. **Signal radical** : compositions typographiques massives, blocs noirs/blancs, signes géométriques et cadence mécanique. Déclinée en thème haut contraste.

La demande complémentaire impose au moins huit identités : loutre, horizon, observatoire abyssal, phosphore CRT, éditorial monochrome, brutaliste, glacier et synthwave. Chaque thème décrit palette, glyphes, cadres, espacement, bannière, mouvement et variantes de composants. La structure des informations et les raccourcis restent stables pour préserver la mémoire des gestes.

## Principes et hypothèses

- Pas de progression inventée : données reçues, mesures locales, « non mesuré » sinon. Aucune note historique en télémétrie courante.
- Le repos ne provoque aucun rafraîchissement périodique. Les transitions expressives sont brèves, interruptibles et limitées à une zone ; cible 60 fps local, réduction SSH/tmux et préférence utilisateur.
- Connexion, cible, session et latence du dernier échange mesuré restent visibles ; une latence non mesurée n'est jamais zéro.
- Couleur toujours accompagnée de mots/signes. Le mode `NO_COLOR` conserve la hiérarchie ; ASCII et pipe n'émettent pas de contrôles de curseur.
- Aucun changement du protocole d'exécution sans preuve de nécessité. La dernière instruction utilisateur autorise la fusion finale sur main après validation ; elle remplace l'interdiction précédente.
- Aucun paquet de moteur lourd ajouté pour un effet visuel. Le spike doit mesurer les options avant une migration de langage.

## Décision de moteur

En attente du spike timeboxé Ratatui / Textual / prompt-toolkit. Mesures à consigner : premier rendu, p50/p95 frame, CPU repos, RSS et octets terminal. Un microbenchmark n'établit pas la fluidité de Terminal.app/iTerm2 physiques.
