<#
.SYNOPSIS
Installe le client Aurora Remote CLI sur Windows.
.DESCRIPTION
Vérifie la présence de Python, installe le package via pip, et affiche les instructions de démarrage.
#>

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  Installation du client Aurora Remote CLI" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

# Vérifier Python
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Erreur: Python 3 est requis mais non installé. Veuillez l'installer depuis python.org." -ForegroundColor Red
    exit 1
}

Write-Host "📦 Installation du paquet aurora-cli..." -ForegroundColor Yellow
python -m pip install . 

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Erreur lors de l'installation via pip." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "✅ Installation terminée avec succès !" -ForegroundColor Green
Write-Host ""
Write-Host "🚀 Pour commencer :" -ForegroundColor Cyan
Write-Host "  1. Obtenez une clé API Bearer sur le serveur Aurora (via /api/ext/key/generate)"
Write-Host "  2. Récupérez l'URL du tunnel Cloudflare (https://xxx.trycloudflare.com)"
Write-Host "  3. Lancez 'aurora connect' pour lier ce client au serveur"
Write-Host "  4. Lancez 'aurora' pour entrer dans le mode interactif"
Write-Host ""
