# Aurora Remote CLI

Aurora Remote CLI est le client terminal officiel pour piloter l'instance **AuroraIA** centrale. Ce client léger permet à n'importe quel développeur ou collaborateur de se connecter au serveur Aurora (hébergé sur une machine distante puissante) sans avoir à installer les modèles lourds (Ollama, ComfyUI, etc.) sur son propre PC.

## 🚀 Fonctionnalités

- **Interface Terminal Moderne** : Affichage riche (couleurs, tableaux, spinners) grâce à `rich`.
- **Missions Autonomes (SSE)** : Streaming en temps réel des actions de l'IA (planification, outils, erreurs).
- **Zéro Rétention de Privilèges** : L'IA demande les mots de passe (Sudo) localement, exécute, et oublie.
- **Diffs de Code Intégrés** : Prévisualisation claire (vert/rouge) de toutes les modifications de fichiers.
- **Multi-plateforme** : Compatible Linux, macOS (Zsh/Bash) et Windows (PowerShell).

## 🛠️ Installation

Le client nécessite **Python 3.10+**. Il pèse moins d'1 Mo.

### Linux / macOS (Zsh & Bash)

```bash
git clone https://github.com/juancodepyandc/aurora-remote-cli.git
cd aurora-remote-cli
chmod +x install.sh
./install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/juancodepyandc/aurora-remote-cli.git
cd aurora-remote-cli
.\install.ps1
```

## 🔗 Connexion (Un clic)

Pour vous connecter au serveur Aurora, l'administrateur (celui qui héberge le serveur) doit vous fournir un **Code d'Invitation**. Ce code gère automatiquement le tunnel sécurisé et l'authentification.

Exécutez simplement :
```bash
aurora connect
```
Puis collez votre code d'invitation lorsqu'il vous est demandé.

## 🎮 Utilisation

### Mode Interactif (REPL)

```bash
aurora
```
*Le mode interactif propose l'autocomplétion, et des commandes internes (commençant par `/`). Tapez `/help` pour voir toutes les commandes.*

### Mode Mission Directe (One-shot)

```bash
aurora run "Analyse le projet React, trouve les fuites de mémoire et corrige les composants."
```

### Commandes Utiles

```bash
aurora status          # Affiche l'état du serveur distant (GPU, modèles...)
aurora doctor          # Lance un diagnostic complet de la connexion
aurora agents          # Liste les agents officiels protégés et dynamiques
```

---
*Fait avec passion pour l'ingénierie logicielle autonome.*
