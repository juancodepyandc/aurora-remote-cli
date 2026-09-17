<#
.SYNOPSIS
Installe le client Aurora Remote CLI sur Windows de façon robuste.
#>

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  Installation du client Aurora Remote CLI" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Host "❌ Erreur: Python 3 est requis mais non installé." -ForegroundColor Red
    exit 1
}

Write-Host "📦 Installation du paquet aurora-cli..." -ForegroundColor Yellow
python -m pip install --user --upgrade --force-reinstall .

if ($LASTEXITCODE -ne 0) {
    Write-Host "❌ Erreur lors de l'installation." -ForegroundColor Red
    exit 1
}

$UserBase = python -c "import site, os; print(os.path.join(site.USER_BASE, 'Scripts'))"
if (-not (Test-Path "$UserBase\aurora.exe")) {
    $UserBase = python -c "import os, sys; print(os.path.dirname(sys.executable))"
}

Write-Host "🔍 Chemin d'installation détecté : $UserBase" -ForegroundColor Cyan

# Ajout dynamique au PATH Windows
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if ($UserPath -notmatch [regex]::Escape($UserBase)) {
    [Environment]::SetEnvironmentVariable("PATH", "$UserPath;$UserBase", "User")
    Write-Host "⚡ Le chemin a été ajouté aux variables d'environnement Windows de façon permanente." -ForegroundColor Green
}

Write-Host ""
Write-Host "✅ Installation terminée avec succès !" -ForegroundColor Green
Write-Host "⚠️  ACTION REQUISE : Fermez et rouvrez cette fenêtre PowerShell pour recharger le PATH." -ForegroundColor Yellow
Write-Host ""
Write-Host "🚀 Ensuite, tapez simplement :" -ForegroundColor Cyan
Write-Host "    jobia connect"
Write-Host ""
