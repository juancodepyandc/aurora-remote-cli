# Captures vérifiables

80 états par renderer : huit thèmes × (streaming à 60/80/120/200 colonnes, accueil/hors-ligne/erreur/permission/résultat à 80, ASCII + NO_COLOR à 60×24). `python/` et `native/` contiennent les textes ; `states/` le JSONL v1 exact alimentant Ratatui. Les PNG natifs couvrent tous les thèmes/largeurs et les accueils ; `index.html` est la galerie. `before/` vient du commit initial e4e0e5a.

Régénération : `python scripts/render_snapshots.py --native native/target/release/aurora-native --output chemin`. Les PNG sont des cellules rasterisées avec une police de développement, pas des captures d’un émulateur macOS physique. Les apparentes jointures de glyphes dépendent de la police de rasterisation.
