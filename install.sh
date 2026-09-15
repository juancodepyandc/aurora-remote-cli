#!/usr/bin/env bash
set -e

echo "==============================================="
echo "  Installation du client Aurora Remote CLI"
echo "==============================================="

if ! command -v python3 &> /dev/null; then
    echo "❌ Erreur: Python 3 est requis mais non installé."
    exit 1
fi

echo "📦 Installation du paquet aurora-cli..."
# Tente d'utiliser pipx si disponible (la meilleure pratique pour les CLI Python)
if command -v pipx &> /dev/null; then
    echo "✨ 'pipx' détecté. Installation isolée..."
    pipx install . --force
else
    echo "⚠️ 'pipx' non détecté. Installation via pip user..."
    python3 -m pip install --user . || python3 -m pip install --user . --break-system-packages
fi

echo ""
echo "✅ Installation terminée avec succès !"
echo "⚠️  Assurez-vous que le dossier local bin (ex: ~/.local/bin) est dans votre PATH."
echo ""
echo "🚀 Pour commencer :"
echo "  1. Obtenez une clé API Bearer sur le serveur Aurora"
echo "  2. Récupérez l'URL du tunnel Cloudflare (https://xxx.trycloudflare.com)"
echo "  3. Lancez 'aurora connect' pour lier ce client au serveur"
echo "  4. Lancez 'aurora' pour entrer dans le mode interactif"
echo ""
