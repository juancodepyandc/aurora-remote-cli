JOBIA 1.5.2 corrige l'accueil masqué lorsqu'une ancienne mission est conservée. Le thème était bien chargé, mais l'avis de reprise fermait l'accueil. Les avis restent maintenant lisibles sous le dessin ; les messages et les détails sont conservés. Aucune mission n'est relancée automatiquement.

Parcours normal sur Mac : `git pull --ff-only`, `./install.sh`, puis `jobia`. Aucun Cargo, export, sélection de thème ou `/home` nécessaire. Otter reste le thème par défaut ; un autre choix volontaire reste respecté. L'installateur distingue maintenant le PATH du terminal appelant de son propre PATH de recherche de Python.

La release compile et teste les binaires sur Mac ARM/Intel, Linux x64 et Windows x64. Après publication, les Mac et Linux passent par le vrai `./install.sh`, vérifient la résolution de `jobia` dans le terminal appelant, puis lancent `jobia` sans argument depuis le paquet installé hors checkout. Le scénario conserve un ancien export invalide, restaure une conversation avec mission et demande incomplète, vérifie l'accueil sans POST automatique, puis teste streaming, coupure et arrêt explicites. Windows vérifie le téléchargement et l'exécution du binaire publié.

Aucun moteur IA ni secret modifié. Les tests utilisent un bridge de démonstration : ils ne prouvent pas une génération 3D réelle ni le fonctionnement du Mac physique de l'utilisateur.
