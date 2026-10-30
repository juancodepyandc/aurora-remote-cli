JOBIA 1.5.1 corrige le statut de mission reçu du serveur (`steps` est un compteur, `plan` une liste), le démarrage automatique avec JOBIA_NATIVE_BIN invalide et l'accès à l'accueil lorsqu'une conversation est restaurée.

Les binaires joints sont compilés et testés sur GitHub pour macOS Apple Silicon, macOS Intel, Linux x86_64 et Windows x86_64. `./install.sh` installe le binaire adapté sans Cargo ; `jobia renderer install` permet de réessayer séparément. Le téléchargement vérifie SHA-256, la version et le démarrage avant remplacement atomique. Aucun téléchargement n'a lieu au lancement ordinaire de `jobia`.

`jobia theme set otter` sélectionne la loutre. `/home` retrouve l'accueil sans effacer les messages ; `/view conversation` revient à l'historique. Les diagnostics de connexion et de reprise restent visibles. Aucun moteur IA ou secret n'est modifié. Les tests simulés du protocole ne prouvent pas la qualité d'une génération 3D réelle ; aucun accès au Mac physique de l'utilisateur.
