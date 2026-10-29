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

Le spike ci-dessous compare Ratatui / Textual / prompt-toolkit. Il ne mesure pas la fluidité des terminaux macOS physiques.

### Spike exécuté et décision (26 octobre)

Trois répétitions séquentielles, PTY Linux 120×32, même bannière animée/saisie/flux, 3 s repos + 3 s à 60 Hz + 3 s repos. Saisie clavier validée dans les neuf exécutions, toutes terminées avec code 0. Go absent, non installé pendant la timebox.

| Médiane truecolor | Rust/Ratatui | Python/Textual | Python/prompt-toolkit |
|---|---:|---:|---:|
| Premier marqueur visible, ms | 4,122 | 110,474 | 71,607 |
| Callback de dessin p50 / p95, ms | 0,132 / 0,191 | 1,955 / 2,620 | 4,711 / 6,232 |
| Période visible p50 / p95, ms | 16,654 / 17,381 | 16,676 / 18,959 | 16,721 / 18,566 |
| CPU actif, % d'un cœur | 0,909 | 16,851 | 31,355 |
| CPU repos, % | 0,000 | 0,357 | 0,000 |
| Pic RSS observé, Mio | 2,895 | 34,551 | 33,316 |
| Octets PTY / 9 s | 44 101 | 1 409 122 | 82 089 |

**Décision : renderer Ratatui léger, moteur et commandes Python conservés**, protocole JSONL local versionné sur stdio. La mémoire/durée du processus Python restent à ajouter ; ces chiffres ne décrivent pas le CLI complet. Le renderer Python reste disponible jusqu'à validation de la parité. Le protocole distant HTTP JSON/SSE existant reste inchangé.

Limites : caches fichiers chauds, CPU résolution 10 ms, callbacks de mesure différents selon bibliothèque, PTY et émulateur pas terminaux physiques. Une cadence de sortie 60 Hz ne prouve pas le rafraîchissement réel du Mac. La série NO_COLOR est conservée séparément ; aucune mesure favorable n'est inventée.

Dépendances : aucun ajout Python en production pour le redesign. Ratatui (MIT) et Crossterm (MIT), serde/serde_json (MIT/Apache-2.0) côté renderer ; Cargo.lock verrouille les versions. Textual, pyte et psutil du spike restent des outils de développement isolés, pas des dépendances du client. Le coût final du binaire et les performances du produit complet seront mesurés après intégration.

## Tokens et composants livrés

Les huit fichiers JSON sont la source des identités ; palette sémantique, fonds, espacements en cellules, cadres, glyphes Unicode/ASCII, bannières et motifs pixels y sont regroupés. Voir `THEMES.md`. Les textes sémantiques dépassent 4,5:1 sur les deux fonds par test sRGB. Le rendu commun expose panel/table/markdown/diff/notice/confirmation/stream et une frontière de texte sûre. Les diffs affichent les numéros des hunks réels ; les messages portent un libellé et un signe. La sortie JSON conserve son chemin sans décor.

Le lint ajouté porte sur les erreurs de correction `E9,F63,F7,F82` dans tout le package et les tests. Il n'existait pas de contrat lint dans ce dépôt : les centaines de recommandations stylistiques héritées d'une configuration extérieure ne sont pas présentées comme corrigées. Deux imports de types différés ont été complétés sans changer les moteurs.

## Auto-critique et intégration

Contre-vérification du 29 octobre : la distinction visuelle des huit thèmes repose encore trop sur la palette, le motif et les bordures ; la composition globale reste proche. Le qualificatif « waouh » n'est pas démontré et ne se déduit pas de huit captures différentes. Des scénarios interactifs manquaient malgré la suite verte : arrêt dans un sélecteur, thème pendant une mission, modèle automatique, reprise et brouillons. Ils sont désormais reproduits puis corrigés. L'accueil 60×24 coupait ses dernières actions ; son texte est raccourci et la colonne de mission vide disparaît à l'accueil natif. Ce lot traite la fonctionnalité et les défauts de lisibilité constatés ; il ne prétend pas résoudre à lui seul l'ambition artistique initiale.

La première capture à 60 colonnes coupait deux cellules du texte : suppression du padding final de Rich, réserve du scrollbar et wrapping corrigés. Le premier renderer natif réutilisait un motif dans sept palettes : remplacé par sept dessins déclaratifs distincts (orbite, rubans, facettes, CRT, monogramme, page, soleil tramé). Le footer étroit a été raccourci sans masquer arrêt/sortie. Les libellés ASCII restants et le suivi de longues lignes ont été corrigés. Le mode 16 couleurs traduit réellement les SGR indexés 0..15 de Crossterm en SGR ANSI classiques.

Le lanceur POSIX est remplacé par Rust pour éviter de garder un second interpréteur Python inactif. Le CLI complet mesure 264–315 ms au premier écran et environ 49 Mio pour Rust + Python sur les quatre profils Linux PTY ; ce sont des mesures ponctuelles, pas des p95 de production. Aucun octet ni CPU mesurable sur la fenêtre de repos de 1,5 s (résolution CPU 10 ms). Binaire release ~1,1 Mo, sans moteur IA embarqué. Les frames p50/p95 du spike restent séparées de ces mesures produit.

Les images Kitty/iTerm2/Sixel sont proposées dans les aperçus, les cellules composables dans le plein écran. Les animations brèves expriment le changement de thème ; les tokens de conversation sont regroupés pour éviter de saturer le terminal. Le thème éditorial reste statique, l’accessibilité prime sur l’effet. Ratatui est verrouillé avec son option rendered-line-info afin de calculer exactement le suivi de lignes repliées ; cette API instable est couverte par le test de resize long.

## Finition des compositions — décision du 29 octobre

Le petit sprite placé à gauche dans tous les thèmes n'apporte pas une identité suffisante. Huit compositions déclaratives sont ajoutées : atelier latéral pour Marée, panorama pour Horizon, scène centrée pour Bathysphère, console P·31, page éditoriale, affiche BLOC, disposition inversée pour Isobare, galerie en perspective pour Minuit FM. Le dessin, le mot-symbole et l'ornement disposent de zones séparées du contenu utile. À faible hauteur, la typographie décorative se retire avant les actions. Aucun texte utile ne s'anime ; aucun minuteur n'est ajouté au repos. Cette décision sera jugée sur les captures des vrais renderers, pas sur une maquette.
