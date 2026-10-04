#!/usr/bin/env bash
# bootstrap.sh — Orders & KDS Backend: one-shot development environment setup
#
# Run this script once after cloning the repository.  It creates the virtual
# environment, installs all dev dependencies, and generates .env from .env.example.
#
# Usage (from the repository root):
#   bash scripts/bootstrap.sh
#   # or, after making it executable:
#   chmod +x scripts/bootstrap.sh && ./scripts/bootstrap.sh
#
# Prerequisites: Python 3.12+ must be on your PATH.
#   macOS:  brew install python@3.12
#   Ubuntu: sudo apt install python3.12 python3.12-venv

set -euo pipefail

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'  # no color

step()    { echo -e "\n${CYAN}==> $1${NC}"; }
ok()      { echo -e "    ${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "    ${YELLOW}[!!]${NC} $1"; }
abort()   { echo -e "\n${RED}[ABORTED]${NC} $1"; exit 1; }

# ---------------------------------------------------------------------------
# Step 1 — Verify Python 3.12+
# ---------------------------------------------------------------------------
step "Checking Python version"

PYTHON_CMD=""
for candidate in python3.12 python3 python; do
    if command -v "$candidate" &>/dev/null; then
        PYTHON_CMD="$candidate"
        break
    fi
done

[ -z "$PYTHON_CMD" ] && abort "Python not found.  Install Python 3.12+ from https://www.python.org"

VERSION_STR=$("$PYTHON_CMD" --version 2>&1)
MAJOR=$(echo "$VERSION_STR" | sed -n 's/Python \([0-9]*\)\.\([0-9]*\).*/\1/p')
MINOR=$(echo "$VERSION_STR" | sed -n 's/Python \([0-9]*\)\.\([0-9]*\).*/\2/p')

if [ -z "$MAJOR" ] || [ -z "$MINOR" ]; then
    abort "Could not parse Python version from: $VERSION_STR"
fi

if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 12 ]; }; then
    abort "Python 3.12+ required.  Found: $VERSION_STR"
fi

ok "Found $VERSION_STR"

# ---------------------------------------------------------------------------
# Step 2 — Create virtual environment
# ---------------------------------------------------------------------------
step "Setting up virtual environment (.venv)"

if [ -f ".venv/bin/python" ]; then
    warn ".venv already exists — skipping creation.  Delete .venv and re-run to recreate it."
else
    "$PYTHON_CMD" -m venv .venv
    ok "Virtual environment created at .venv/"
fi

VENV_PYTHON=".venv/bin/python"
VENV_PIP=".venv/bin/pip"

# ---------------------------------------------------------------------------
# Step 3 — Upgrade pip inside the venv
# ---------------------------------------------------------------------------
step "Upgrading pip"
"$VENV_PYTHON" -m pip install --upgrade pip --quiet
ok "pip is up to date"

# ---------------------------------------------------------------------------
# Step 4 — Install dev dependencies
# ---------------------------------------------------------------------------
step "Installing dependencies (requirements/dev.txt)"
"$VENV_PIP" install -r requirements/dev.txt
ok "All packages installed"

# ---------------------------------------------------------------------------
# Step 5 — Generate .env
# ---------------------------------------------------------------------------
step "Generating .env from .env.example"

if [ -f ".env" ]; then
    warn ".env already exists — skipping generation.  Run to overwrite:"
    warn "  $VENV_PYTHON scripts/setup_env.py --force"
else
    "$VENV_PYTHON" scripts/setup_env.py
fi

# ---------------------------------------------------------------------------
# Done — print next steps
# ---------------------------------------------------------------------------
echo ""
echo -e "${GREEN}========================================"
echo -e "  Bootstrap complete!"
echo -e "========================================${NC}"
echo ""
echo "Next steps:"
echo ""
echo "  1. Activate the virtual environment in your terminal:"
echo -e "       ${YELLOW}source .venv/bin/activate${NC}"
echo ""
echo "  2. Select the interpreter in your IDE:"
echo -e "       ${YELLOW}VS Code: Ctrl+Shift+P > 'Python: Select Interpreter' > .venv${NC}"
echo ""
echo "  3. Edit .env and set SAGA_TIMEOUT_SECONDS (agree value with team — OP-01)."
echo ""
echo "  4. Start infrastructure:"
echo -e "       ${YELLOW}docker compose up -d postgres redis rabbitmq${NC}"
echo ""
echo "  5. Apply database migrations:"
echo -e "       ${YELLOW}alembic upgrade head${NC}"
echo ""
echo "  6. Start the API (terminal 1):"
echo -e "       ${YELLOW}uvicorn app.main:app --reload --port 8000${NC}"
echo ""
echo "  7. Start background workers (terminal 2):"
echo -e "       ${YELLOW}python -m app.workers.run_all${NC}"
echo ""
echo -e "  API docs : http://localhost:8000/docs"
echo -e "  RabbitMQ : http://localhost:15672"
echo ""
