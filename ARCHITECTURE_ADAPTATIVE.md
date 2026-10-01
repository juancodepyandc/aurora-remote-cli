# Adapter JOBIA à la tâche, à la machine et aux progrès techniques

Audit du 1 octobre 2026. Le dépôt comportait déjà de nombreuses modifications
locales : elles ont été conservées. Ce document distingue les corrections
logicielles réalisées des capacités qui restent à mesurer ou à construire.

## Pourquoi les résultats peuvent être imparfaits

Le problème principal est le lien incomplet entre la demande, le moteur choisi
et la mesure de qualité. Changer de modèle ou augmenter sa résolution ne suffit
pas à établir qu'un résultat représente mieux le sujet demandé.

Constats vérifiés dans le code avant les corrections :

- `EvolutionLoop.improve()` promouvait le premier candidat admissible sans
  comparer les suivants. L'ordre de découverte déterminait donc le choix.
- `should_promote()` acceptait un score identique au score courant. La boucle
  pouvait réannoncer une progression sans amélioration mesurée.
- Un seul résultat courant était conservé par capacité. Les demandes, fichiers
  de référence et environnements différents partageaient une comparaison.
- Les manifestes d'adaptateurs existaient, mais les chemins image et 3D
  construisaient directement leurs commandes. Modifier le manifeste ne
  modifiait donc pas réellement ces exécutions.
- La résolution d'adaptateurs utilisait des sous-chaînes de noms de modèles :
  un modèle d'un autre auteur pouvait être pris pour un modèle compatible.
- La détection de plateforme assimilait un OS inconnu à Linux et une libc
  inconnue à glibc. ROCm pouvait être décrit comme CUDA.
- Le relevé de mémoire dépendait de mécanismes POSIX ; il ne disposait pas
  d'une mesure fiable de RAM physique sur Windows.
- Le score des sondes image et 3D mesure la disponibilité technique. Une image
  valide et non uniforme peut obtenir 1,0 tout en représentant le mauvais
  personnage. Un GLB valide et texturé peut également obtenir 1,0 avec une
  silhouette incorrecte.
- Le score géométrique présent dans `application/output/3d/score_history.jsonl`
  reste à 79,7 avant/après réparation sur l'exemple conservé. Cette trace ne
  démontre ni une amélioration, ni la fidélité au sujet demandé.
- Le module cyber côté client est principalement un routage vers un serveur.
  Le serveur et son évaluateur ne sont pas inclus dans ce dépôt.

## Socle corrigé

Les exécutions image et 3D utilisent désormais `AdapterRegistry.build()` et
l'environnement du runner déclaré. Les paramètres 3D du chemin normal sont
déclarés dans `policies/adapters.toml`. Les noms de modèles correspondent
exactement aux identités ou alias déclarés ; un adaptateur spécifique prime sur
un adaptateur générique. Une liste d'arguments peut être déclarée directement,
ce qui préserve notamment les chemins Windows contenant des espaces.

L'identité du modèle image est transmise au worker indépendamment du chemin
de son cache. Un snapshot au nom opaque ne doit pas faire perdre les réglages
propres à Turbo ou Schnell.

La boucle compare tous les candidats distincts rencontrés dans les passes
bornées avant de promouvoir un seul gagnant. Un score égal ne remplace pas un
résultat courant dont les preuves sont encore valides. Les ex æquo conservent
l'ordre de découverte ; la préférence pour les installations locales est
déclarée en politique. Avec les sondes techniques actuelles, plusieurs candidats
valides auront le même score : cela ne démontre pas une égalité visuelle.
Une nouvelle exécution équivalente du moteur courant peut renouveler ses
preuves sans être annoncée comme une promotion ni prolonger artificiellement
la boucle d'amélioration.

Les comparaisons sont séparées par demande, empreinte du fichier de référence,
contrôles requis, version de métrique, environnement détecté et manifeste
d'adaptateurs. Les résultats de ces contextes sont conservés. Les preuves du
résultat courant sont revérifiées avant de lui accorder un avantage.
L'environnement décrit notamment OS, architecture, Python, libc, accélération
détectée et capacité mémoire. Il ne constitue pas encore un relevé complet des
versions de pilotes et des bibliothèques des environnements de chaque moteur,
ni des révisions et empreintes de l'ensemble des poids utilisés.

Les poids, contrôles et limites des sondes sont déclarés dans
`policies/quality.toml`. Une préférence image apprise peut être utilisée par
`select_image_model()` si les preuves correspondent encore à l'environnement et
si le budget mémoire connu du modèle tient dans la RAM disponible. Une preuve
expirée, modifiée ou incomplète ne sert plus de recommandation. La durée de
validité est configurable ; elle ne déclenche pas une nouvelle recherche toute
seule. Les modèles au budget mémoire inconnu ne remplacent pas automatiquement
les choix de production.

La mesure de RAM utilise `psutil`, avec les anciens mécanismes comme secours.
Les OS et libc inconnus restent inconnus ; ROCm est distingué de CUDA lorsqu'il
est identifié par PyTorch. La détection ne garantit pas que le runtime isolé
d'un moteur dispose du même backend que le CLI : sa sonde réelle reste nécessaire.
La résolution sur Apple silicon fonctionne aussi avant l'installation de
PyTorch. Les contraintes Python sont évaluées avec `packaging`, y compris leurs
bornes supérieures et exclusions. La matrice Intel macOS déclare une pile
PyTorch ancienne plutôt qu'une version récente sans binaire officiel pour Intel ;
elle demande encore des recettes de modèles compatibles avec cette pile.

Les sondes de domaines supplémentaires peuvent être fournies par des paquets
installés avec un entry point `jobia.probes`. L'ajout d'un domaine demande aussi
son runner, sa politique d'évaluation et sa source de candidats. La recherche de
modèles ne fournit jamais du code automatiquement chargé comme évaluateur.

Exemple de déclaration dans le `pyproject.toml` d'une extension :

```toml
[project.entry-points."jobia.probes"]
audio = "mon_extension.evaluation:probe_audio"
```

La fonction reçoit un `Candidate` et les entrées de son test. Elle doit exécuter
le candidat demandé et retenir son résultat, son identité, ses contrôles, sa
métrique versionnée et ses empreintes. Une sonde de disponibilité peut utiliser
`capability_probe._record()` avec une politique adaptée. Un évaluateur de qualité
sémantique doit produire et justifier ses propres mesures calibrées. Déclarer un
entry point seul n'implémente pas un moteur ni un évaluateur audio ou cyber.

## Architecture à poursuivre pour tous les modules

```text
Demande + contraintes de l'utilisateur
              ↓
Profil de machine + inventaire des runtimes réellement exécutables
              ↓
Recettes candidates : moteurs, versions, paramètres et étapes
              ↓
Exécution isolée → validation technique → évaluation du domaine
              ↓
Comparaison sur les mêmes références et métriques
              ↓
Choix documenté + historique + réévaluation des futurs candidats
```

La recette complète doit être l'unité de sélection. En 3D, la qualité dépend
aussi de la référence, du détourage, de la reconstruction, de la texturation et
des rendus de contrôle. Un classement du générateur seul manque ces interactions.

| Domaine | Mesures nécessaires au-delà de la validité du fichier |
|---|---|
| Image | Identité, composition, attributs demandés, texte lisible, diversité des cas |
| 3D | Silhouette sur plusieurs vues, proportions, géométrie, UV, placement des textures et PBR |
| Code | Exécution de tests du projet, contraintes de l'environnement, absence de régressions |
| Cyber | Exactitude sur des cas autorisés et reproductibles, preuves des constats, faux positifs et faux négatifs |
| Audio | Intelligibilité, contenu demandé, prononciation, stabilité et durée |
| Vidéo | Cohérence temporelle, mouvements, identité et synchronisation |

Chaque métrique doit déclarer sa version, ses références et ses limites. Les
tests qui servent à choisir un moteur doivent être distincts des cas réservés
à mesurer ses progrès. Il faut plusieurs tâches et répétitions, pas seulement
un résultat favorable. Le coût, la latence et le pic de mémoire doivent être
mesurés à côté de la qualité : la meilleure recette dépend des contraintes de
la tâche et de la machine.

Pour éviter les choix figés, le catalogue Python et la table de modes distante
restent à remplacer par des métadonnées de capacités, un catalogue actualisable
et une sélection de recettes évaluées. Certains réglages spécifiques aux familles
de modèles restent également dans les workers. Les informations stables sur les
API et formats appartiennent aux adaptateurs ; les préférences et seuils doivent
être déclarés et les classements doivent venir de mesures comparables.

L'adaptateur Hunyuan reste le seul moteur 3D intégré au provisioning courant.
La sélection apprise est reliée au chemin image ; les autres chemins de
production doivent encore être reliés à leurs recettes et évaluateurs.
`experience.py` mémorise des succès/échecs de configurations ; une boucle
d'apprentissage complète par domaine reste à intégrer.

La recherche quotidienne doit proposer des candidats avec une provenance et
une version précises. Leur adoption demande une exécution dans un environnement
isolé, des comparaisons et un retour possible à la recette précédente. Les
réparations suggérées par la boucle actuelle sont consignées mais ne sont pas
automatiquement appliquées par `EvolutionLoop`.

Une couche de raisonnement peut ensuite proposer des hypothèses et des recettes.
Le résultat de ses expériences doit déterminer si ses propositions sont gardées.
Ce système peut améliorer ses choix et accumuler de l'expérience ; il ne suffit
pas à créer une AGI, ni à garantir des résultats parfaits.

## Portabilité et validation

L'objectif réaliste est un CLI portable avec découverte et diagnostics, puis
des moteurs compatibles avec les possibilités réelles de chaque hôte.
Toutes les versions d'OS ne peuvent pas être garanties : les dépendances imposent
leurs propres versions minimales et backends disponibles. La documentation
[PyTorch](https://pytorch.org/get-started/locally/) décrit ces différences de
plateforme et d'accélération.
L'arrêt des builds Intel macOS est documenté par l'
[annonce de l'équipe PyTorch](https://dev-discuss.pytorch.org/t/pytorch-macos-x86-builds-deprecation-starting-january-2024/1690).
Les contraintes de versions utilisent les règles de
[Packaging](https://packaging.pypa.io/en/stable/specifiers.html).

Les extensions utilisent le mécanisme standard documenté par
[Python Packaging](https://packaging.python.org/en/latest/guides/creating-and-discovering-plugins/).
La séparation des métriques, comparaisons et évaluateurs est également documentée
par [Hugging Face Evaluate](https://huggingface.co/docs/evaluate/en/index).

La suite initiale passait : 462 tests, 2 ignorés. Les nouveaux tests couvrent les
régressions de sélection, contextes d'évaluation, expiration, preuves altérées,
utilisation réelle des manifestes, passage des chemins et extension de sondes.
Les plateformes absentes et les moteurs lourds sont simulés dans ces tests.
Après les corrections : 494 tests passent et 2 sont ignorés. La résolution de
`torch` sur le Mac arm64 réel avec Python 3.14 fonctionne avant son installation.
Cette validation logicielle ne mesure pas une amélioration des rendus image/3D
réels et ne certifie pas Windows, Linux, audio ou cyber sur du matériel réel.

Commande de vérification : `.venv/bin/python -m pytest -q`.

## Corrections de l'incident figurine et de la continuité des sessions

Le cas réel a révélé des défauts supplémentaires aux interfaces entre étapes :

- Une destination au milieu de la phrase (`dans Documents un model 3d…`)
  n'était pas extraite. Le parseur sépare désormais sujet et destination,
  y compris cette orthographe, et résout le dossier Documents de la plateforme.
  Un `output_dir` explicitement fourni par l'appelant conserve sa priorité.
- Les essais annoncés en float32 rechargeaient encore le modèle en float16.
  Le dtype de chargement suit maintenant chaque essai. L'échantillonneur est
  libéré avant le décodage géométrique, dont la précision et le backend sont
  configurables indépendamment. Monter toute la pile en float32 sur le GPU
  Metal réel a dépassé son budget : cette variante n'est pas une solution
  portable démontrée.
- Le décodeur hiérarchique installé calculait les positions physiques avec
  le dtype entier des indices : la taille des voxels devenait zéro. Le nouveau
  décodeur conserve les indices entiers mais calcule des coordonnées flottantes,
  contrôle les valeurs finies et synchronise ses lots de requêtes. Il a été
  exécuté sur un champ de sphère connu. Ce test ne prouve pas la qualité des
  latents produits par le générateur neuronal.
- Des latents réels finis sont maintenant conservés avec empreintes pour les
  reprises. Les fichiers de forme et leurs noms réels sont recopiés entre
  tentatives ; une interruption de la peinture ne doit pas effacer la forme.
  Les empreintes des poids et révisions de modèles restent à compléter.
- L'exporteur installé appliquait deux conversions de l'axe V des UV. Un test
  réel d'export/réimport vérifie son contrat et n'ajoute une correction que si
  elle est nécessaire. Le rendu utilisait aussi la convention de texture
  opposée à celle de Trimesh et retournait verticalement la caméra ; ces
  conventions ont été corrigées et testées sur des GLB réellement exportés.
- Le calcul du budget de peinture dépendait d'un nom de module inexistant.
  Le backend mesuré est transmis explicitement ; la configuration de résolution
  est maintenant effectivement appliquée. Une texture demandée ne peut plus
  être silencieusement remplacée par une forme nue si le moteur manque.

La livraison 3D demande un GLB structurellement valide, des rendus de plusieurs
vues, les contrôles géométriques et de placement de texture, puis une revue du
sujet sur un rendu du maillage réel. Les rapports sont conservés et hashés ; une
livraison antérieure n'est pas réutilisée si sa preuve a disparu ou changé.
Les seuils d'IoU, de volume et de placement restent des heuristiques à calibrer
sur plusieurs sujets. Un faible `bbox_fill` ne démontre pas à lui seul qu'un
objet est une plaque ; des objets ajourés ou ouverts peuvent aussi avoir un
faible score. Ni ces métriques ni un VLM ne garantissent la perfection visuelle.

Les images autonomes utilisent le même worker et le contrôle visuel. Les
réponses du vérificateur doivent être complètes, contenir des observations
distinctes et ne pas contredire leur propre verdict. Une réponse tronquée ou
malformée entraîne une reprise bornée de la revue du même fichier, pas une
nouvelle génération supposée nécessaire. Le modèle visuel est choisi dans le
catalogue selon le budget disponible ; le test local a exposé les contradictions
de l'ancien vérificateur. Après rejet visuel, le brief peut être réécrit à partir
des défauts observés, et une autre recette installée compatible peut être
essayée. Son rang dans le catalogue ne constitue pas une preuve de supériorité.
Les longues demandes Stable Diffusion sont encodées en segments compatibles
avec les tokenizers installés plutôt que tronquées silencieusement.
Une demande ainsi conservée n'est pas pour autant comprise parfaitement : les
essais ont notamment produit des sujets multiples après des prompts de correction
trop longs. Les briefs sont désormais bornés, et une critique déjà incorporée
au brief n'est pas réinjectée intégralement dans le prompt image. Les images
originales restent intactes ; l'entrée de revue est une réduction traçable dont
la dimension maximale est déclarée en politique. Cette revue peut manquer des
défauts de détail. Les vues 3D sont regroupées en planche de contrôle pour que le
VLM examine aussi les angles latéraux et arrière, pas seulement la meilleure vue.

Les conversations sont archivées intégralement par espace de travail, reprises
au redémarrage et partagées par les deux interfaces locales. `/clear` efface
seulement l'écran ; `/new` ouvre une nouvelle archive sans supprimer la précédente.
Lorsque le contexte ne suffit plus, des résumés explicitement faillibles et des
échanges originaux pertinents sont fournis au modèle. L'archive est complète,
pas le contexte du modèle : ce mécanisme ne garantit pas une mémoire parfaite.
Une coupure de flux ou une limite de génération n'est plus annoncée comme une
réponse complète. Les continuations sont bornées ; les réponses interrompues
restent en diagnostic au lieu de devenir des faits dans l'historique.

### Conversion d’une image fournie : incident du 1er octobre

Le checkpoint `3b159ac5a180/.jobia/8d4a7de3516672b4/job.json` montrait une
erreur d’entrée, pas seulement un mauvais classement des modèles : le chemin
de `candidate-1.png` avait été transmis comme une description au modèle de
texte. Celui-ci avait inventé « Eve », puis le pipeline avait généré une
nouvelle référence sans ouvrir l’image fournie, qui représentait une loutre.

La requête distingue désormais `input_image`, les instructions et la
destination. Pour une conversion image → 3D, le fichier est copié sans changer
ses octets dans le dossier du travail, décodé réellement dans l’environnement
Pillow et lié à l’identité du travail par SHA-256. Le brief, la génération
d’image et la revue sémantique d’une référence générée sont sautés : l’image
de l’utilisateur fait autorité. Les contrôles du maillage texturé et la
comparaison de ses rendus avec cette image restent obligatoires. Un fichier
manquant, corrompu, animé ou intégralement transparent est refusé, jamais
remplacé par une image inventée. Une référence explicitement désignée mais
non résolue est également refusée. Les formats restent ceux que le décodeur
installé sait ouvrir ; il n’y a pas de garantie sur tous les codecs.

Les chemins avec espaces ou guillemets, relatifs, et un retour de ligne copié
après un séparateur sont couverts. Un chemin Windows ne devient pas un faux
chemin relatif sur macOS/Linux. Le pont actuel ne transportant pas les pièces
jointes locales, ces conversions restent explicitement locales au lieu
d’envoyer un chemin inaccessible au PC distant. Le changement de contenu
du fichier source crée un autre travail et ne réutilise pas la forme précédente.

Un essai réel a confirmé l’import sans génération du PNG original 1024×1024,
empreinte `853270bda2f5c5261da25253bdba16503b44d1d256c3d1afdc35dcf993741702`.
Il reste un échec de qualité/exécution, pas une livraison : la reconstruction
à 384 puis 512 produit une grande plaque sous le sujet (`bbox_fill=0.0439`),
visible sur la planche multivue. La tentative d’échantillonnage entièrement
float32 dépasse ensuite la limite MPS (18.05 GiB alloués pour 17.76 GiB permis).
Aucun seuil n’a été abaissé et aucune limite mémoire désactivée pour accepter
ce résultat. Cet incident reste conservé sous
`3b159ac5a180/.jobia/8eb0fada7759b887/job.json`, avec
`attempt-0001/shape-final-diagnostic.json` et `model.shape.views.png`.
La correction du routage ne certifie donc pas que le moteur 3D actuel convient
à toutes les références ni que sa stratégie de reprise mémoire est adaptée.
Vérification de cette correction : 560 tests réussis dans l’environnement CLI
(11 ignorés), et 56 tests réussis dans l’environnement numérique du moteur,
dont les 8 tests de décodage réel des entrées. Les deux suites se recouvrent.

Un essai réel avec `qwen2.5:7b` a sauvegardé puis rouvert une conversation et
retrouvé le projet HELIOS, le budget de 73 euros et l'absence d'abonnement.
Les essais réels 3D de cet incident n'ont pas encore établi une figurine
texturée livrable : les tentatives et diagnostics restent conservés. Les refus
ne sont pas présentés comme des résultats réussis.

Le test de contrôle sur `assets/example_images/379.png` du moteur installé a
réussi la reconstruction sur Metal au premier essai : 753 878 triangles,
`bbox_fill=0.0967`, échantillonnage float16 et décodage float32 sur MPS. Ce test
sans `PYTHONPATH` fourni a aussi révélé et corrigé un chemin d'import du worker
isolé après son changement de dossier. La forme conservée a ensuite été
réutilisée pour produire un GLB texturé de 40 000 triangles avec atlas 4096.
Le contrôle a mesuré `bbox_fill=0.0959`, IoU de silhouette `0.6458`, corrélation
de couleur `0.3521` et erreur de palette `0.0273`. Le placement est classé
`partial`, pas `placed` : le test ne constitue donc pas une livraison validée.
Cela sépare la réussite technique de reconstruction/texturation du contrôle
visuel final, et ne résout pas à lui seul l'absence de Noël dans les images de
Spider-Man. Les rendus et le rapport de ce contrôle sont conservés dans
`outputs/3d/d61e9da6b140/adapter-verification` du répertoire de données local.
La comparaison VLM entre cette référence et la planche multivue a dépassé son
délai ; son verdict sémantique est donc indisponible, pas favorable par défaut.
La calibration du contrôle de placement et la stabilité de cette revue restent
des travaux nécessaires avant d'annoncer un résultat 3D validé de bout en bout.

La voix dispose maintenant des adaptateurs locaux séparés décrits ci-dessous ;
leur qualité perceptuelle n'est pas encore évaluée de bout en bout.
Les tâches cyber outillées reposent sur le pont et nécessitent des tests
du serveur et de ses permissions. Les réponses académiques bénéficient du
contexte conservé et d'instructions de traçabilité, mais aucun vérificateur
général de citations, calculs et preuves n'est encore intégré. Ces modules ne
sont donc pas certifiés fonctionnels par les tests image/3D ou conversation.

Vérification logicielle de la passe précédente : 525 tests CLI réussis,
3 ignorés dans son environnement léger ; 48 tests numériques de rendu,
placement et décodage réussis dans l'environnement réel du moteur. Ces deux
exécutions se recouvrent : leurs nombres ne constituent pas un total de tests
uniques. La conservation de conversation a également été vérifiée avec un
modèle réel, tandis que plusieurs contrats de reprise utilisent des doubles
de test. Le vérificateur `qwen3-vl:8b` a été installé localement pour les essais
visuels. Les références réellement générées pour l'incident continuent d'être
refusées lorsqu'elles oublient la variante Noël ou ajoutent des éléments non
demandés ; aucun GLB Spider-Man terminé n'a été annoncé.

Les contrats de rendu s'appuient sur la
[spécification glTF](https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html),
et la revue structurée sur les contrats officiels
[Ollama](https://docs.ollama.com/capabilities/structured-outputs).

## Sélection et remplacement multi-module — nouvelle passe

`core/model_selection.py` est la politique commune aux rôles. Elle vérifie les
adaptateurs déclarés, le système, l'accélérateur, la VRAM dédiée, la RAM libre et
le disque avant de proposer une recette. La RAM unifiée n'est pas comptée comme
VRAM CUDA. Une préférence du catalogue ou un score de disponibilité technique
n'est jamais appelé « meilleure qualité ».

`jobia choose ROLE --json` expose les choix et motifs de rejet sans installation.
`--install` prépare réellement la recette sélectionnée. La génération image et
3D peut remplacer une recette échouée dans une limite déclarée ; une sélection
3D explicite reste respectée. La référence acceptée est conservée entre moteurs
avec leurs checkpoints séparés. Aucun ancien moteur n'est supprimé automatiquement.
Les erreurs sont mémorisées par environnement et recette ; les rejets visuels
sont limités au sujet concerné. Une nouvelle recette ou l'expiration du délai
permet une nouvelle évaluation, sans prétendre entraîner le modèle.

Le catalogue et le worker image intègrent FLUX.2 klein 4B avec sa classe
`Flux2KleinPipeline`, Diffusers >= 0.37, quatre étapes et guidance 1, conformément
au [modèle publié](https://huggingface.co/black-forest-labs/FLUX.2-klein-4B).
Les paramètres incompatibles ne sont plus transmis silencieusement. Aucun
test de génération FLUX.2 n'a été exécuté ici ; sa qualité n'est pas certifiée.

TRELLIS.2 a désormais son propre worker et provisionneur ; sélectionner
`microsoft/TRELLIS.2-4B` ne lance plus Hunyuan. La recette native possède des
révisions Git immuables, des environnements isolés, des journaux de compilation,
des sondes d'import/CUDA, et un export GLB avec attributs PBR et textures PNG.
Elle n'exécute pas le `setup.sh` système du fournisseur. Les composants du
pipeline sont préparés séparément et reliés localement. Le détourage utilise
BiRefNet, alternative ouverte du wrapper officiel : code, configuration et
révision sont explicitement fixés et vérifiés par SHA-256. Cela n'est pas une
preuve d'équivalence ou de supériorité face à RMBG-2.0. L'encodeur DINOv3 peut
exiger une licence acceptée sur Hugging Face ; JOBIA ne l'accepte pas au nom
de l'utilisateur. L'adaptateur n'a pas été exécuté sur GPU CUDA dans cette passe.
Les [prérequis officiels](https://github.com/microsoft/TRELLIS.2#prerequisites)
sont Linux, CUDA et 24 Go de VRAM NVIDIA. Le Mac de cet incident ne les satisfait
pas ; le service Pinokio du PC n'était pas joignable lors du contrôle.

Le rasteriseur CPU fourni contrôle maintenant les rendus d'autres moteurs sans
dépendre des plugins Hunyuan. Ses tests numériques vérifient profondeur commune,
coordonnées barycentriques corrigées de la perspective et orientation des faces.
Les seuils de géométrie, UV, textures, silhouette et revue sémantique restent
inchangés. Le modèle de loutre de l'incident n'est toujours pas une livraison
texturée validée.

`jobia audio --text 'Bonjour.'` prépare la narration française VITS/MMS ;
`jobia audio --input fichier.wav` prépare Whisper pour la transcription.
Les fichiers ne sont pas écrasés et les entrées trop longues ne sont pas
tronquées. La synthèse contrôle les valeurs finies, le silence et la saturation,
pas encore l'intelligibilité. MMS français est sous licence CC-BY-NC-4.0,
donc non commerciale, sans clonage. Ces workers n'ont pas fait l'objet d'un
essai neuronal réel dans cette passe. La musique ne reçoit plus à tort un
modèle de transcription.

Les conversations choisissent leurs modèles servis en fonction du rôle et du
budget déclaré. Un modèle en échec peut être remplacé avant toute émission ;
après émission partielle, l'interruption est signalée et archivée sans mélanger
deux réponses. Le contexte reste conservé. Le routage académique/cyber utilise
ce socle, mais ni exactitude générale ni permissions du serveur ne sont ainsi
certifiées. Les profils de recherche couvrent image, 3D, texte, vision, voix et
transcription ; une architecture inconnue reste une proposition sans exécution
automatique de son code. Il ne s'agit pas d'une AGI ou d'un apprentissage autonome
sans évaluateur et sans limites.

Vérification finale de cette passe : 602 tests CLI réussis, 12 ignorés faute de
bibliothèques numériques dans cet environnement léger ; 60 tests de décodage,
rendu, rasterisation et placement réussis avec les bibliothèques installées du
moteur. Ces suites se recouvrent. La construction du wheel a réussi et son
contenu inclut les nouveaux workers et les manifestes, sans les reçus de machine
ni les sorties de génération. Ces tests n'attestent pas de la qualité neuronale
de FLUX.2, TRELLIS.2, VITS ou Whisper sur cette machine.
