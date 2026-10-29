# Contre-vérification fonctionnelle du 29 octobre

Référence de départ : `f893b6f`, version 1.5.0. Le retour utilisateur sur une livraison insuffisamment travaillée a motivé cette vérification. La suite initiale passait (1213 Python, 14 Rust) mais omettait plusieurs interactions. Les nouveaux tests ont été exécutés en échec avant les correctifs.

| Parcours | Défaut reproduit / correction | Preuve |
|---|---|---|
| Arrêter depuis un sélecteur | Ctrl+C était intercepté ; il atteint désormais la commande stop | Test clavier Rust et PTY étendu natif/Python |
| Thème et vues en mission | Les commandes slash étaient bloquées ; une liste explicite autorise la présentation sans remplacer la tâche active | Tests passerelle/renderer et PTY avec deux créations explicites et un arrêt |
| Reprise au démarrage | Le backend natif omettait les avis de mission conservée ; initialisation commune sans envoi automatique | Test du serveur JSONL, historique temporaire |
| Modèle automatique | L'option manquait dans le sélecteur natif | Test sélection modèle, valeur vide explicite |
| Historique de saisie | ↑ puis ↓ effaçait le brouillon courant ; il est conservé | Test de saisie Rust |
| Demande récupérée | `/retry` incomplet préparait un brouillon invisible en natif ; restitution Unicode versionnée, sans écraser une saisie commencée | Tests passerelle et renderer, aucune mission exécutée |
| Connexion indisponible | Cause stockée mais absente du corps visible ; diagnostic affiché avec action de reprise | Tests état/connexion et capture de renderer |
| Accueil compact | Dernières actions coupées en 60×24 ; texte raccourci et retours Markdown explicites | 16 captures compactes, commandes /modules /missions /theme /help visibles |
| Renderer réellement disponible | L'installation Python seule ne livre pas Rust ; doctor le signale | Contrôle local de présence, sans téléchargement |

Validation locale finale : 1216 tests Python réussis, 18 ignorés et 5 sous-tests ; 19 tests Rust ; Ruff, fmt, clippy -D warnings, wheel/sdist et build release réussis. Les contrôles GitHub du commit de la PR complètent la preuve multiplateforme.

176 captures (88 par renderer) régénérées par `scripts/render_snapshots.py`, avec les huit thèmes, les largeurs 60/80/120/200, les états de connexion, permission et résultat, puis ASCII/NO_COLOR et accueil 60×24. Le manifeste `snapshots.json` conserve les empreintes des textes ; les 16 accueils compacts sont publiés dans `captures/`. PNG = rasterisation de cellules, pas capture d'un terminal Mac physique.

Les JSON/textes `native/` et `python/` viennent de vrais PTY Linux et d'un faux bridge avec socket interrompue, saisie Unicode et resize. Le repos de 1,5 s ne produit aucun octet et aucun CPU discernable à la résolution de 10 ms. Les durées ponctuelles à caches chauds ne sont pas des p95.

Le bridge réel a répondu HTTP 200/ok sur `/api/health`, puis HTTP 401 sur `/api/cli/doctor` sans identité. Aucun secret ni configuration réelle lu/modifié pour cette requête, aucune mission IA envoyée, aucun service redémarré. La liaison authentifiée du Mac, les générations des modèles et les protocoles graphiques sur terminaux physiques restent non validés.

**Verdict esthétique : exigence encore ouverte.** Les compositions restent trop proches entre thèmes ; les tests fonctionnels et palettes distinctes ne démontrent pas le résultat « waouh ». Ce lot corrige les défauts observés et documente les preuves, sans prétendre achever la direction artistique ni appliquer le plan moteur de Phase 2.
