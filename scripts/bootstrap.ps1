# bootstrap.ps1 -- Orders & KDS Backend: one-shot development environment setup
#
# Run this script once after cloning the repository.  It creates the virtual
# environment, installs all dev dependencies, and generates .env from .env.example.
#
# Usage (from the repository root):
#   .\scripts\bootstrap.ps1
#
# If PowerShell blocks execution due to policy, run first (once per machine):
#   Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
#
# Prerequisites: Python 3.12+ must be on your PATH.
#   Download from https://www.python.org/downloads/

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host "    [OK] $Message" -ForegroundColor Green
}

function Write-Warn {
    param([string]$Message)
    Write-Host "    [!!] $Message" -ForegroundColor Yellow
}

function Abort {
    param([string]$Message)
    Write-Host ""
    Write-Host "[ABORTED] $Message" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# Step 1 -- Verify Python 3.12+
# ---------------------------------------------------------------------------
Write-Step "Checking Python version"

$pythonCmd = $null
foreach ($candidate in @("python", "python3", "py")) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $pythonCmd = $candidate
        break
    }
}

if (-not $pythonCmd) {
    Abort "Python not found.  Install Python 3.12+ from https://www.python.org/downloads/"
}

$versionOutput = & $pythonCmd --version 2>&1
if ($versionOutput -match "Python (\d+)\.(\d+)") {
    $major = [int]$Matches[1]
    $minor = [int]$Matches[2]
    if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) {
        Abort "Python 3.12+ required.  Found: $versionOutput"
    }
    Write-Ok "Found $versionOutput"
} else {
    Abort "Could not determine Python version from: $versionOutput"
}

# ---------------------------------------------------------------------------
# Step 2 -- Create virtual environment
# ---------------------------------------------------------------------------
Write-Step "Setting up virtual environment (.venv)"

$venvPath = ".venv"

if (Test-Path "$venvPath\Scripts\python.exe") {
    Write-Warn ".venv already exists -- skipping creation.  Delete .venv and re-run to recreate it."
} else {
    & $pythonCmd -m venv $venvPath
    Write-Ok "Virtual environment created at $venvPath\"
}

$venvPython = ".\$venvPath\Scripts\python.exe"
$venvPip    = ".\$venvPath\Scripts\pip.exe"

# ---------------------------------------------------------------------------
# Step 3 -- Upgrade pip inside the venv
# ---------------------------------------------------------------------------
Write-Step "Upgrading pip"
& $venvPython -m pip install --upgrade pip --quiet
Write-Ok "pip is up to date"

# ---------------------------------------------------------------------------
# Step 4 -- Install dev dependencies
# ---------------------------------------------------------------------------
Write-Step "Installing dependencies (requirements/dev.txt)"
& $venvPip install -r requirements\dev.txt
Write-Ok "All packages installed"

# ---------------------------------------------------------------------------
# Step 5 -- Generate .env
# ---------------------------------------------------------------------------
Write-Step "Generating .env from .env.example"

if (Test-Path ".env") {
    Write-Warn ".env already exists -- skipping generation."
    Write-Warn "To overwrite, run: $venvPython scripts\setup_env.py --force"
} else {
    & $venvPython scripts\setup_env.py
}

# ---------------------------------------------------------------------------
# Done -- print next steps
# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "  Bootstrap complete!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host ""
Write-Host "  1. Activate the virtual environment:" -ForegroundColor White
Write-Host "       .venv\Scripts\Activate.ps1" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  2. Select the interpreter in VS Code:" -ForegroundColor White
Write-Host "       Ctrl+Shift+P > Python: Select Interpreter > .venv" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  3. Edit .env -- set SAGA_TIMEOUT_SECONDS (agree with team, OP-01)." -ForegroundColor White
Write-Host ""
Write-Host "  4. Start infrastructure:" -ForegroundColor White
Write-Host "       docker compose up -d postgres redis rabbitmq" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  5. Apply database migrations:" -ForegroundColor White
Write-Host "       alembic upgrade head" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  6. Start the API (terminal 1):" -ForegroundColor White
Write-Host "       uvicorn app.main:app --reload --port 8000" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  7. Start background workers (terminal 2):" -ForegroundColor White
Write-Host "       python -m app.workers.run_all" -ForegroundColor DarkYellow
Write-Host ""
Write-Host "  API docs : http://localhost:8000/docs" -ForegroundColor DarkCyan
Write-Host "  RabbitMQ : http://localhost:15672" -ForegroundColor DarkCyan
Write-Host ""
