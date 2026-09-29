$ErrorActionPreference = "Stop"
python -m venv "$PSScriptRoot\.venv"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& "$PSScriptRoot\.venv\Scripts\python.exe" -m pip install --upgrade $PSScriptRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$JobiaBin = "$PSScriptRoot\.venv\Scripts"
$UserPath = [Environment]::GetEnvironmentVariable("PATH", "User")
if (($UserPath -split ';') -notcontains $JobiaBin) {
    [Environment]::SetEnvironmentVariable("PATH", "$JobiaBin;$UserPath", "User")
}
Write-Host "JOBIA installé. Rouvre ton terminal puis tape jobia."
