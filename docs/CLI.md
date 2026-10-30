# Installer et vérifier l'interface

Dans une copie Git propre du CLI sur le Mac :

```sh
git pull --ff-only
./install.sh
jobia
```

`install.sh` installe le renderer compilé sur GitHub pour la plateforme : Mac Apple Silicon ou Intel, Linux x64, Windows x64. **Cargo n'est pas nécessaire.** L'empreinte SHA-256, la version et le démarrage du binaire sont contrôlés avant son installation atomique dans `.venv/libexec/jobia/<version>/`. La ligne `renderer` du reçu indique le résultat. Si le téléchargement échoue, l'interface Python reste disponible et le motif est affiché ; relancer `./install.sh` réessaie aussi cette étape.

Aucun téléchargement ni compilation au lancement de `jobia`. Aucun export ni nettoyage de variable requis. Un ancien `JOBIA_NATIVE_BIN` vers un fichier absent ne bloque plus le mode auto : le binaire géré est recherché, puis le repli Python. Une demande explicite `--renderer native` échoue clairement si aucun binaire n'est disponible.

Pour compiler volontairement depuis les sources avec Rust/Cargo (toolchain de validation : 1.96.1), utiliser `cargo build --release --locked --manifest-path native/Cargo.toml`, puis lancer **sur une seule commande** `JOBIA_NATIVE_BIN="$PWD/native/target/release/aurora-native" .venv/bin/jobia ui --renderer native`. L'ancienne procédure qui exportait un chemin après un échec de compilation est retirée.

Le thème par défaut est otter. Un autre thème enregistré reste respecté ; F2 ou `/theme` permettent de le changer, facultativement. Au lancement, l'accueil reste visible avec une conversation ou une mission conservée. Les avis de reprise occupent la zone d'activité ; `/view conversation` retrouve les messages et les détails. Aucun travail n'est relancé automatiquement. Une erreur de connexion ouvre son diagnostic. `/home` est un raccourci de navigation, pas une étape d'installation.

L'installateur expose `jobia` via le lanceur de la venv et les chemins système accessibles sur Mac. Il vérifie le PATH du terminal appelant, distinct des chemins temporaires utilisés pour trouver Python. `.venv/bin/jobia` reste disponible comme chemin explicite de diagnostic. Les options suivantes sont facultatives :

```sh
jobia theme list
jobia theme preview otter
jobia theme set otter
jobia --theme brutal ui
NO_COLOR=1 JOBIA_UNICODE=0 jobia ui
jobia --color never --help
jobia --color never ui | cat
```

F2 ou /theme : aperçu en direct, flèches, Entrée garde, Échap restaure. /theme glacier applique directement. F3 modèles, F4 vues, F5 diagnostic, Alt+1..6 vues directes, Ctrl+C arrêt, Ctrl+Q sortie, Alt+Entrée nouvelle ligne. Dans Rust, PgUp/PgDown parcourent la sortie, Ctrl+Home/End début/suivi ; ↑↓ rappellent la saisie. Dans Python, Tab donne le focus à la sortie pour la parcourir.

Tests dans le checkout :

```sh
.venv/bin/python -m pip install -e '.[test,dev]' pyte
.venv/bin/python -m pytest tests -q
.venv/bin/python -m ruff check aurora_cli tests scripts
.venv/bin/python -m build --no-isolation
cargo fmt --manifest-path native/Cargo.toml --check
cargo clippy --manifest-path native/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path native/Cargo.toml --locked
.venv/bin/python scripts/validate_terminal.py
.venv/bin/python scripts/validate_terminal.py --extended
.venv/bin/python scripts/validate_terminal.py --renderer python --depth none --extended
.venv/bin/python scripts/render_snapshots.py --native native/target/release/aurora-native
```

La validation PTY lance uniquement un faux serveur loopback et une configuration temporaire ; elle coupe réellement le flux HTTP puis vérifie la reprise sans nouveau POST. Elle ne consomme aucun calcul AuroraIA. Les PNG sont des rasterisations des cellules du renderer : pas des captures du Mac physique. Les paramètres du terminal réel restent à vérifier sur Terminal.app, iTerm2, tmux et SSH.

`--extended` soumet ensuite une deuxième mission distincte dans le scénario, change le thème pendant son exécution et demande son arrêt depuis le sélecteur : exactement deux créations et un arrêt doivent être observés. `jobia doctor` indique désormais si le renderer natif est trouvé : une installation Python seule n'installe pas le binaire Rust. Recompiler le renderer après une mise à jour de ses sources ; un ancien binaire ne reçoit pas automatiquement les correctifs clavier.
