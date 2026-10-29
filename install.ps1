<#
.SYNOPSIS
  One-command setup of t0-powerbi on Windows: Python environment, package, t0-gamma weights, self-test.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File .\install.ps1

.PARAMETER Python
  Python to build the environment from, as a version for the `py` launcher ("3.11") or a path to python.exe.
  Default: the first of 3.11, 3.12, 3.13, 3.10 found.

.PARAMETER VenvPath
  Where to create the environment. Default: .venv next to this script.

.NOTES
  Needs internet for the one-time downloads (PyTorch ~120 MB, t0-gamma ~1 GB). Afterwards forecasting is offline.
  Re-running is safe: an existing environment is reused and only missing pieces are installed.
#>
param(
    [string]$Python = "",
    [string]$VenvPath = ""
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
if (-not $VenvPath) { $VenvPath = Join-Path $root ".venv" }

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "`nERROR: $text" -ForegroundColor Red; exit 1 }

# Run a native command and stop on a non-zero exit code (PowerShell does not do that by itself).
function Run {
    & $args[0] $args[1..($args.Length - 1)]
    if ($LASTEXITCODE -ne 0) { Fail "Command failed (exit code $LASTEXITCODE): $($args -join ' ')" }
}

# --- 1. Find a supported Python (3.10-3.13; 3.11 is the tested one) ---------------------------------
Step "Looking for Python"
function Test-Python($exe, $launcherVersion) {
    try {
        $args2 = @()
        if ($launcherVersion) { $args2 += "-$launcherVersion" }
        $args2 += @("-c", "import sys; print('%d.%d' % sys.version_info[:2])")
        $out = & $exe @args2 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
        $v = [version]($out | Select-Object -First 1)
        if ($v.Major -eq 3 -and $v.Minor -ge 10 -and $v.Minor -le 13) { return $v }
    } catch { }
    return $null
}

$pyExe = $null; $pyVer = $null; $pyLauncherArg = $null
if ($Python -and (Test-Path $Python)) {
    $pyExe = $Python; $pyVer = Test-Python $pyExe $null
    if (-not $pyVer) { Fail "$Python is not Python 3.10-3.13." }
} else {
    $candidates = if ($Python) { @($Python) } else { @("3.11", "3.12", "3.13", "3.10") }
    foreach ($c in $candidates) {
        $v = Test-Python "py" $c
        if ($v) { $pyExe = "py"; $pyLauncherArg = $c; $pyVer = $v; break }
    }
}
if (-not $pyExe) {
    Fail "No Python 3.10-3.13 found. Install Python 3.11 from https://www.python.org/downloads/ (tick 'py launcher'), then run this script again."
}
Write-Host "Using Python $pyVer"

# --- 2. Create the environment ------------------------------------------------------------------------
$venvPython = Join-Path $VenvPath "Scripts\python.exe"
if (Test-Path $venvPython) {
    Step "Reusing environment $VenvPath"
} else {
    Step "Creating environment $VenvPath"
    if ($pyLauncherArg) { Run py "-$pyLauncherArg" -m venv $VenvPath } else { Run $pyExe -m venv $VenvPath }
}

# --- 3. Install the package (pulls tfc-t0[torch], pandas, matplotlib) -------------------------------
Step "Installing t0-powerbi and its dependencies (PyTorch is large; this takes a few minutes)"
Run $venvPython -m pip install --upgrade pip --quiet
Run $venvPython -m pip install -e "$root" --quiet

# --- 4. Download t0-gamma once and check it forecasts -------------------------------------------------
Step "Downloading t0-gamma (~1 GB, once) and running a test forecast"
Run $venvPython -m t0_powerbi.model

# --- 5. What to do in Power BI --------------------------------------------------------------------------
$scripts = Join-Path $VenvPath "Scripts"
Write-Host "`nDone." -ForegroundColor Green
Write-Host @"

Next, in Power BI Desktop:
  File > Options and settings > Options > Python scripting
  > Detected Python home directories: Other, then browse to:

      $scripts

  (If Power BI does not accept a virtual environment, see powerbi/setup.md, step 4.)
Then follow powerbi/dashboard_guide.md.

Check the integration without Power BI:
  $venvPython powerbi\simulate_refresh.py examples\sales.csv
"@
