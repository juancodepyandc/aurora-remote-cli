# Vue active — 31 octobre 2026

80 captures texte : huit thèmes × deux renderers × (60×24, 80×32, 120×30, 200×60 et ASCII/NO_COLOR à 60×24). Treize PNG sélectionnés, dont les huit thèmes natifs à 120 colonnes. Ce sont les cellules des renderers, pas des captures physiques du Mac. Illustration, conversation et plan coexistent pendant la demande. Les fichiers sont reproductibles avec `scripts/render_snapshots.py --work-only --native native/target/release/aurora-native`.

Deux scénarios PTY dans `pty/` : mission restaurée, zéro envoi au démarrage, streaming, coupure SSE, reprise avec curseur, resize, changement de thème en mission, masquage du JSON de contrôle et arrêt explicite. Les captures `*-active.txt` proviennent du vrai terminal pendant l'exécution. Faux bridge local, aucune inférence. Relevés ponctuels : environ 133/135 ms jusqu'à la première frame, 51/50 Mio de RSS arborescente au pic ; zéro CPU mesuré et zéro octet au repos sur 1,5 s. Ce ne sont pas des percentiles ni des mesures sur le Mac de l'utilisateur.

Tests : 1414 Python réussis, 18 ignorés, cinq sous-tests ; 22 Rust. Ruff, fmt, clippy et build wheel/sdist réussis. Le test du thème pendant l'activité échouait avant correction. Le scénario Intel installé attend désormais la persistance effective du thème au lieu d'accéder prématurément à sa clé. La CI et la release restent nécessaires pour conclure au parcours installé Mac ARM/Intel.

Limites : les animations restent brèves et désactivées après la transition, statiques dans le repli Python. Le bandeau compact ne remplace pas l'illustration détaillée des écrans larges. Les générations réelles dépendent du moteur et de ses ressources ; ces captures ne prouvent pas leur réussite.
