# Thèmes du terminal

Le thème par défaut est **Marée · Loutre** (`otter`). La loutre tient une coquille nacrée au-dessus de lignes de marée. Sa palette turquoise/bois flotté, ses glyphes, ses espacements et ses mouvements sont distincts des sept autres identités.

| Identifiant | Identité | Composition et mouvement |
|---|---|---|
| `otter` | Marée · Loutre | Mascotte pixel, eau, arrondis, onde courte |
| `jobia` | Horizon | Ruban boréal, traits fins, apparition progressive |
| `abyss` | Bathysphère | Orbites et braille, cadres doubles, respiration |
| `plain` | Édition | Monochrome, grands blancs, typographie, immobile |
| `phosphor` | P·31 | Phosphore vert, crochets carrés, frappe/balayage bref |
| `brutal` | BLOC | Haut contraste noir/blanc/jaune, masses épaisses, signal bref |
| `glacier` | Isobare | Facettes obliques, air et angles, révélation froide |
| `synthwave` | Minuit FM | Soleil tramé, perspective, rose et corail, onde |

## Choisir

```sh
jobia theme list
jobia theme preview otter
jobia theme set glacier
jobia --theme brutal ui
JOBIA_THEME=synthwave jobia
```

En session : `/theme` ou F2 ouvre le choix ; l'aperçu suit la sélection, Entrée valide, Échap annule. `/theme nom` applique directement. Ordre de priorité au lancement : option explicite, `JOBIA_THEME`, configuration, défaut. Le nom `jobia` reste accepté ; `loutre` désigne `otter`, `nexus` et `cyber` désignent `jobia`.

## Déclarer une nouvelle identité

Copier un fichier de `aurora_cli/themes/data/`, changer son nom et son `id`. Le registre découvre les JSON automatiquement ; aucun écran ni table de noms à modifier.

- `schema: 1`, `id`, `label`, `description` : identité.
- `palette` : `primary`, `secondary`, `accent`, `text`, `muted`, `border`, `success`, `warning`, `error`, `info`, `banner`, `banner_alt`, `surface`, `raised`.
- `glyphs` : chaque signe sémantique est une paire `[unicode, ascii]`.
- `spacing` : `gutter`, `panel_x`, `panel_y`, `section`, `prompt_rows`, `sidebar_width`, en cellules.
- `box` : `minimal`, `rounded`, `square`, `heavy`, `double`, `ascii`.
- `components` : variante d'en-tête, rail, séparateur, marqueurs d'onglets, spinner, grille pixel et palette pixel.
- `banner` : art Unicode, art ASCII obligatoire, effet, décorations, durée maximale et légende.
- `motion` : budget bref, préférences de mouvement. Pas de typewriter pour le contenu utile ni l'aide.

La grille pixel est une liste de lignes, chaque caractère hexadécimal indexant `pixel_palette`. Elle est convertie sans bibliothèque d'image en PNG natif, Sixel, demi-blocs ou ASCII. Ne jamais encoder une donnée reçue d'un modèle comme commande terminal.

Valider avec `python -m pytest tests/test_design_system.py tests/test_themes.py tests/test_animation.py -q`, puis régénérer les snapshots et inspecter les quatre largeurs. Le contraste sRGB des textes sémantiques doit rester ≥ 4,5:1 sur les deux fonds.

## Dégradation et mouvement

`NO_COLOR=1` a priorité sur la palette. `JOBIA_COLOR=ansi|color256|truecolor|never` force la profondeur pour diagnostic. `JOBIA_UNICODE=0` impose ASCII. `JOBIA_ANIM=none|reduced|full` règle le mouvement ; un pipe ne reçoit jamais d'animation. La cadence maximale locale est 60 Hz, réduite sur SSH. Aucun rafraîchissement périodique décoratif au repos.

Kitty et iTerm2 sont détectés par leurs variables terminal ; Sixel par le type de terminal ou `JOBIA_IMAGES=sixel`. `JOBIA_IMAGES=none` désactive les images. Sous tmux, dans un pipe ou en NO_COLOR, les protocoles d'images sont désactivés pour préserver le flux. L'aperçu de thème est la surface prévue pour les images ; le plein écran privilégie les cellules composables afin de conserver un diff de rendu fiable. Un terminal qui n'annonce pas ses capacités reçoit les blocs Unicode, puis ASCII si nécessaire. Aucun dialogue bloquant de détection n'est envoyé.

Les tests des encodeurs et profils simulés ne constituent pas une validation physique de tous les émulateurs. Les bornes réelles de couleur, police et rafraîchissement appartiennent au terminal de l'utilisateur.
