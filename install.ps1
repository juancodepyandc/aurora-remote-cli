# Thin shim. The install logic lives in aurora_cli/install.py so that macOS,
# Linux and Windows all run the same steps; this file locates or provisions
# an optimal Python interpreter before handing over to aurora_cli/install.py.
$ErrorActionPreference = "Stop"
$JobiaRoot = $PSScriptRoot

function Find-JobiaPython([int]$MinMinor) {
    $candidates = @()
    if ($env:PYTHON_BIN) { $candidates += $env:PYTHON_BIN }
    if ($env:JOBIA_PYTHON) { $candidates += $env:JOBIA_PYTHON }
    $venvPy = Join-Path $JobiaRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPy) { $candidates += $venvPy }
    $pyLauncher = Get-Command "py" -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        foreach ($ver in @("3.13", "3.12", "3.11", "3.10", "3.9")) {
            try {
                $found = & $pyLauncher.Source "-$ver" -c "import sys; print(sys.executable)" 2>$null
                if ($LASTEXITCODE -eq 0 -and $found) { $candidates += $found.Trim() }
            } catch {}
        }
    }
    $candidates += @("python3.12", "python3.11", "python3.10", "python", "python3")
    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $resolved = if (Test-Path $candidate) { $candidate } else { (Get-Command $candidate -ErrorAction SilentlyContinue).Source }
        if ($resolved) {
            try {
                & $resolved -c "import sys; raise SystemExit(sys.version_info < (3, $MinMinor))" 2>$null
                if ($LASTEXITCODE -eq 0) { return $resolved }
            } catch {}
        }
    }
    return $null
}

$selectedPython = Find-JobiaPython 10
if (-not $selectedPython) {
    $uvCmd = Get-Command "uv" -ErrorAction SilentlyContinue
    if ($uvCmd) {
        try {
            & $uvCmd.Source python install 3.12 2>$null
            $uvPy = & $uvCmd.Source python find 3.12 2>$null
            if ($LASTEXITCODE -eq 0 -and $uvPy) { $selectedPython = $uvPy.Trim() }
        } catch {}
    }
    if (-not $selectedPython) {
        $selectedPython = Find-JobiaPython 8
    }
}

if ($selectedPython) {
    & $selectedPython "$JobiaRoot\aurora_cli\install.py" --python $selectedPython --root $JobiaRoot --receipt "$JobiaRoot\install-receipt.json" @args
    $installCode = $LASTEXITCODE
    if ($installCode -eq 0) {
        $scriptDir = Join-Path $JobiaRoot ".venv\Scripts"
        if (($env:Path -split ';') -notcontains $scriptDir) { $env:Path = "$scriptDir;$env:Path" }
    }
    exit $installCode
}

Write-Error "Python 3 est requis pour installer JOBIA."
exit 1

