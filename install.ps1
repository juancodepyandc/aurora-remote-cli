# Thin shim. The install logic lives in aurora_cli/install.py so that macOS,
# Linux and Windows all run the same steps; this file only picks a Python.
$ErrorActionPreference = "Stop"
$JobiaRoot = $PSScriptRoot
$candidates = @()
if ($env:PYTHON_BIN) { $candidates += $env:PYTHON_BIN }
$candidates += @("python", "python3", "py")
foreach ($candidate in $candidates) {
    if (-not $candidate) { continue }
    $resolved = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($resolved) {
        & $resolved.Source -c 'import sys; raise SystemExit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) {
            & $resolved.Source "$JobiaRoot\aurora_cli\install.py" --root $JobiaRoot --receipt "$JobiaRoot\install-receipt.json" @args
            $installCode = $LASTEXITCODE
            if ($installCode -eq 0) {
                $scriptDir = Join-Path $JobiaRoot ".venv\Scripts"
                if (($env:Path -split ';') -notcontains $scriptDir) { $env:Path = "$scriptDir;$env:Path" }
            }
            exit $installCode
        }
    }
}
Write-Error "Python 3.10 ou plus est requis pour installer JOBIA."
exit 1
