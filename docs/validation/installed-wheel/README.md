# Validation du paquet installé

27 octobre 2026, Linux. Wheel `jobia_cli-1.4.0-py3-none-any.whl` reconstruite par `python -m build --no-isolation`, puis extraite avec `zipfile` dans un répertoire temporaire hors checkout. L'interpréteur dispose des dépendances de test ; ce n'est pas un test d'installation des dépendances depuis zéro.

Le scénario de `scripts/validate_terminal.py` est exécuté avec `ROOT` dirigé vers ce répertoire, qui contient la wheel et un lien local vers le binaire release. Le processus enfant charge donc le package extrait, reçoit `JOBIA_NATIVE_BIN` explicitement et s'exécute dans un autre dossier temporaire. Il utilise exclusivement le faux bridge loopback et une configuration jetable.

Résultat : code 0, aperçu/annulation et changement persisté de thème, resize 80→60, saisie Unicode, coupure HTTP et reprise au curseur 1, un seul POST de mission. Aucun octet au repos sur 1,5 s ; CPU non discernable à la résolution de 10 ms. Le premier écran mesuré à 193,876 ms est une mesure ponctuelle avec caches chauds, pas un p95 ni une mesure Mac. Voir le JSON et la capture texte adjacents.
