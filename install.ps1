<#
.SYNOPSIS
Installe le client Aurora Remote CLI sur Windows.
.DESCRIPTION
Vérifie la présence de Python, installe le package via pipx ou pip, et affiche les instructions.
#>

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  Installation du client Aurora Remote CLI" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Erreur: Python 3 est requis mais non installé. (python.org)" -ForegroundColor Red
    exit 1
}

Write-Host "📦 Installation du paquet aurora-cli..." -ForegroundColor Yellow

if (Get-Command "pipx" -ErrorAction SilentlyContinue) {
    Write-Host "✨ 'pipx' détecté. Installation isolée..." -ForegroundColor Cyan
    pipx install . --force
} else {
    Write-Host "⚠️ 'pipx' non détecté. Installation via pip user..." -ForegroundColor Yellow
    python -m pip install --user .
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Erreur lors de l'installation." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "✅ Installation terminée avec succès !" -ForegroundColor Green
Write-Host "⚠️  Assurez-vous que votre dossier Python Scripts est dans votre variable d'environnement PATH." -ForegroundColor Yellow
Write-Host ""
Write-Host "🚀 Pour commencer :" -ForegroundColor Cyan
Write-Host "  1. Obtenez une clé API Bearer sur le serveur Aurora"
Write-Host "  2. Lancez 'aurora connect' pour configurer l'URL et la clé"
Write-Host "  3. Lancez 'aurora' pour entrer dans le mode interactif"
Write-Host ""
