# Installer et vérifier l'interface

Dans une copie Git propre du CLI sur le Mac :

```sh
git switch main
git pull --ff-only
./install.sh
cargo build --release --locked --manifest-path native/Cargo.toml
export JOBIA_NATIVE_BIN="$PWD/native/target/release/aurora-native"
.venv/bin/jobia ui --renderer native
```

`cargo` doit être installé. Le toolchain utilisé pour la validation est Rust 1.96.1 ; les dépendances sont verrouillées. L'installation Python reste indépendante : si Rust est absent, `jobia ui --renderer python` conserve l'interface Python, et `jobia ui --text` le mode linéaire. Le mode auto utilise le binaire local s'il existe, sans téléchargement ni compilation au démarrage.

Le binaire est recherché dans `JOBIA_NATIVE_BIN`, puis à côté des sources Python dans `native/target/release/aurora-native`, puis PATH. Cette deuxième recherche couvre l'installation éditable ; `install.sh` installe un paquet dans la venv, d'où le chemin explicite ci-dessus. Une distribution wheel Python ne contient pas de binaire spécifique à un OS ; reconstruire le renderer depuis le checkout ou l'archive source. `JOBIA_NATIVE_BIN` exporté reste actif dans ce terminal ; le redéfinir dans un nouveau terminal, ou installer le binaire dans un dossier de PATH. `JOBIA_RENDERER=auto|native|python` est disponible ; l'option `ui --renderer` prime.

`.venv/bin/jobia` utilise directement l'installation du checkout. L'installateur expose aussi `jobia` dans `~/.local/bin` ; les commandes courtes ci-dessous supposent ce dossier dans PATH.

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
