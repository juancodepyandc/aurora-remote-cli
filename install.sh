#!/bin/bash
set -e

echo "==============================================="
echo "  Installation du client Aurora Remote CLI"
echo "==============================================="

if ! command -v python3 &> /dev/null; then
    echo "❌ Erreur: Python 3 est requis mais non installé."
    exit 1
fi

if ! command -v pip3 &> /dev/null; then
    echo "❌ Erreur: pip3 est requis mais non installé."
    exit 1
fi

echo "📦 Installation du paquet aurora-cli..."
# On force l'installation de la CLI globale (dépend des systèmes, on utilise --break-system-packages si besoin sur les linux récents ou un env virtuel pour simplifier)
# La méthode propre moderne est pipx, mais on fait simple pour le moment :
pip3 install . || pip3 install . --break-system-packages

echo ""
echo "✅ Installation terminée avec succès !"
echo ""
echo "🚀 Pour commencer :"
echo "  1. Obtenez une clé API Bearer sur le serveur Aurora (via /api/ext/key/generate)"
echo "  2. Récupérez l'URL du tunnel Cloudflare (https://xxx.trycloudflare.com)"
echo "  3. Lancez 'aurora connect' pour lier ce client au serveur"
echo "  4. Lancez 'aurora' pour entrer dans le mode interactif"
echo ""
