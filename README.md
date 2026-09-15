# Aurora Remote CLI

Aurora Remote CLI est un client terminal léger, rapide et agnostique conçu pour piloter votre instance centrale **AuroraIA** à distance. Que vous soyez sur macOS, Windows, ou Linux, ce client vous donne le contrôle total de votre copilote sans jamais avoir à installer de modèles lourds (Ollama, ComfyUI, etc.) sur votre machine locale.

## 🚀 Fonctionnalités

- **Interface Terminal Moderne** : Affichage riche (couleurs, tableaux, spinners) grâce à `rich` et `prompt-toolkit`.
- **Missions Autonomes (SSE)** : Streaming en temps réel des actions de l'IA (planification, utilisation d'outils, erreurs).
- **Zéro Rétention de Privilèges** : L'IA demande les mots de passe (Sudo) via un prompt sécurisé localement, les transmet pour exécution, et le serveur les oublie instantanément.
- **Diffs de Code Intégrés** : Prévisualisation visuelle et claire (vert/rouge) de toutes les modifications de fichiers effectuées par l'IA.
- **Écosystème Connecté** : 
  - Prise en charge des **Agents Dynamiques** (créés à la volée, sauvegardables).
  - Protocol **MCP** (Model Context Protocol) supporté nativement.
  - Architecture **Skills** pour une compréhension parfaite de vos projets métier.
  - **Connexions Sécurisées** (GitHub, Canva, Vercel, Figma...).
- **Multi-plateforme** : Compatible Linux, macOS (Zsh/Bash) et Windows (PowerShell).

## 🛠️ Installation

Le client nécessite **Python 3.10+**. Il pèse moins d'1 Mo et n'installe aucune dépendance d'IA lourde.

### Linux / macOS (Zsh & Bash)

```bash
git clone https://github.com/VOTRE_ORG/aurora-remote-cli.git
cd aurora-remote-cli
chmod +x install.sh
./install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/VOTRE_ORG/aurora-remote-cli.git
cd aurora-remote-cli
.\install.ps1
```

## ⚙️ Configuration Initiale

Une fois installé, vous devez lier le client à votre serveur Linux central Aurora.

1. Démarrez votre serveur Aurora principal.
2. Récupérez l'URL du tunnel Cloudflare (ex: `https://mon-tunnel.trycloudflare.com`).
3. Générez une clé API Bearer sur le serveur.
4. Lancez la configuration côté client :

```bash
aurora connect
```

L'outil vous demandera l'URL et votre clé. La configuration est stockée localement dans `~/.aurora/config.json`.

## 🎮 Utilisation

### Mode Interactif (REPL)

Lancez simplement la commande `aurora` pour entrer dans l'interface conversationnelle et agentique.

```bash
aurora
```
*Le mode interactif propose l'autocomplétion, la coloration syntaxique, et des commandes internes (commençant par `/`). Tapez `/help` pour voir toutes les commandes.*

### Mode Mission Directe (One-shot)

Si vous savez exactement ce que vous voulez, lancez une mission autonome d'une seule traite :

```bash
aurora run "Analyse le projet React dans le dossier courant, trouve les fuites de mémoire et corrige les composants fautifs."
```

### Commandes Utiles

```bash
aurora status          # Affiche l'état du serveur distant (GPU, modèles, VRAM...)
aurora doctor          # Lance un diagnostic complet de la connexion
aurora agents          # Liste les 37 agents officiels protégés et vos agents dynamiques
aurora mcp list        # Liste les serveurs MCP découverts
aurora skills list     # Liste les compétences (Skills) actives
```

## 🔐 Sécurité & Éthique (Humanized Git)

Aurora est conçu pour s'intégrer dans des équipes de développement professionnelles. 
- Les commits poussés sur vos dépôts utilisent des conventions standards (ex: `feat:`, `fix:`). 
- Aucun message du type "Généré par une IA" n'est inséré dans votre code ou vos commits. 
- La prise de décision de l'agent est transparente dans votre CLI, mais invisible sur vos dépôts.

## 🤝 Contribution

Les contributions sont les bienvenues ! 
1. Forkez le projet
2. Créez votre branche (`git checkout -b feature/AmazingFeature`)
3. Commitez de manière standard et humaine (`git commit -m 'feat: add some amazing feature'`)
4. Pushez (`git push origin feature/AmazingFeature`)
5. Ouvrez une Pull Request.

---
*Fait avec passion pour l'ingénierie logicielle autonome.*
