#!/usr/bin/env bash
set -e

echo "==============================================="
echo "  Installation du client Aurora Remote CLI"
echo "==============================================="

if ! command -v python3 &> /dev/null; then
    echo "❌ Erreur: Python 3 est requis."
    exit 1
fi

echo "📦 Installation du paquet aurora-cli (Force Upgrade)..."
# Force-reinstall to bypass pip cache and ensure the code updates
python3 -m pip install --user --upgrade --force-reinstall . || python3 -m pip install --user --upgrade --force-reinstall . --break-system-packages

# Trouver dynamiquement l'emplacement exact de l'exécutable
USER_BIN=$(python3 -c "import site, os; print(os.path.join(site.USER_BASE, 'bin'))" 2>/dev/null || echo "$HOME/.local/bin")

# Si non trouvé, faire une recherche (spécifique aux Mac avec des environnements python multiples)
if [ ! -f "$USER_BIN/aurora" ]; then
    FOUND_AURORA=$(find ~/Library/Python ~/.local/bin /usr/local/bin /opt/homebrew/bin -maxdepth 4 -name "aurora" -type f -perm +111 2>/dev/null | head -n 1)
    if [ -n "$FOUND_AURORA" ]; then
        USER_BIN=$(dirname "$FOUND_AURORA")
    fi
fi

echo "🔍 Chemin d'exécutable détecté : $USER_BIN"

add_to_path() {
    local rc_file="$1"
    # On crée le fichier s'il n'existe pas, particulièrement pour .zshrc sur Mac
    if [ ! -f "$rc_file" ] && [[ "$rc_file" == *".zshrc"* || "$rc_file" == *".bashrc"* ]]; then
        touch "$rc_file"
    fi
    if [ -f "$rc_file" ]; then
        if ! grep -q "PATH=\"$USER_BIN" "$rc_file"; then
            echo "" >> "$rc_file"
            echo "export PATH=\"$USER_BIN:\$PATH\"" >> "$rc_file"
        fi
    fi
}

add_to_path "$HOME/.zshrc"
add_to_path "$HOME/.bashrc"
add_to_path "$HOME/.bash_profile"

echo ""
echo "✅ Installation terminée avec succès !"
echo "⚡ Le chemin $USER_BIN a été ajouté à votre configuration de terminal."
echo "⚠️  ACTION REQUISE : Pour pouvoir utiliser la commande 'aurora' immédiatement, tapez ceci :"
if [[ "$SHELL" == *"zsh"* ]]; then
    echo "    source ~/.zshrc"
else
    echo "    source ~/.bashrc"
fi
echo ""
echo "🚀 Ensuite, connectez-vous avec :"
echo "    aurora connect"
echo ""
