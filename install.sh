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

# Upgrade pip to avoid UNKNOWN-0.0.0 issues on Mac
python3 -m pip install --upgrade pip --user 2>/dev/null || true

# Install the package
python3 -m pip install --user . || python3 -m pip install --user . --break-system-packages

# Determine Bin Path and add to profiles
MAC_BIN="$HOME/Library/Python/3.9/bin"
LINUX_BIN="$HOME/.local/bin"

add_to_path() {
    local rc_file="$1"
    if [ -f "$rc_file" ]; then
        if ! grep -q "$MAC_BIN" "$rc_file"; then
            echo "export PATH=\"$MAC_BIN:\$PATH\"" >> "$rc_file"
        fi
        if ! grep -q "$LINUX_BIN" "$rc_file"; then
            echo "export PATH=\"$LINUX_BIN:\$PATH\"" >> "$rc_file"
        fi
    fi
}

add_to_path "$HOME/.zshrc"
add_to_path "$HOME/.bashrc"
add_to_path "$HOME/.bash_profile"

export PATH="$MAC_BIN:$LINUX_BIN:$PATH"

echo ""
echo "✅ Installation terminée avec succès !"
echo "⚡ Le terminal a été configuré automatiquement pour que la commande 'aurora' soit accessible de n'importe où."
echo "Si 'aurora' n'est pas reconnu immédiatement, redémarrez votre terminal ou tapez: source ~/.zshrc"
echo ""
echo "🚀 Utilisation :"
echo "  Connectez-vous avec : aurora connect <URL_DU_TUNNEL>"
echo "  Ensuite, tapez juste  : aurora"
echo ""
