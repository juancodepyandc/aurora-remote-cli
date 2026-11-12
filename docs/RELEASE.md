JOBIA 1.7.0 rend la connexion autonome : après un redémarrage d'Aurora, le client lit l'adresse publiée au commit courant, suit le PC pour toutes ses requêtes, reprend la mission interrompue depuis son checkpoint et se reconnecte seul dans l'interface. L'appairage se fait dans l'interface (`/connect`), avec un code affiché.

Les serveurs MCP s'importent depuis Claude Desktop, Claude Code, Cursor, VS Code, Windsurf, Gemini CLI et Codex, se cherchent (outils, prompts, ressources, registre officiel MCP) et s'installent depuis le registre ; ils s'exécutent localement en stdio, HTTP Streamable ou SSE, ou sur le PC. Les skills s'importent depuis un dossier, une archive, une URL ou un dépôt GitHub, se cherchent et s'activent par conversation.

L'interface gagne un bandeau d'état animé, des onglets Skills et MCP, la palette Ctrl+K, des notifications, une carte d'appairage, une jauge colorée et une file de demandes pendant une mission. Les nouvelles configurations utilisent FULL ; SAFE reste disponible et respecté.

Binaires natifs : macOS ARM/Intel, Linux x64 (glibc et musl statique), Linux ARM64, Windows x64. L'installateur essaie les variantes adaptées et recrée l'environnement Python même sans `ensurepip`.

Tests : suite Python, tests Rust et validations PTY contre un bridge de démonstration. Ils ne prouvent ni un redémarrage réel du PC, ni une génération, ni le comportement des paquets MCP tiers.
