#!/usr/bin/env bash
# ===========================================
# Dev Toolkit Setup — Linux / macOS / WSL
# ===========================================
set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}============================================${NC}"
echo -e "${CYAN}  Free Dev Toolkit — Environment Setup${NC}"
echo -e "${CYAN}============================================${NC}"
echo ""

# Check Python version
echo -e "${CYAN}[1/6]${NC} Checking Python..."
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}ERROR: Python 3 not found. Install Python 3.10+ first.${NC}"
    echo "  → https://www.python.org/downloads/"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo -e "  Found Python ${GREEN}${PYTHON_VERSION}${NC}"

# Create virtual environment
echo -e "\n${CYAN}[2/6]${NC} Creating virtual environment..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    echo -e "  ${GREEN}Created .venv${NC}"
else
    echo -e "  ${YELLOW}Already exists, skipping${NC}"
fi

# Activate
source .venv/bin/activate
echo -e "  ${GREEN}Activated${NC}"

# Install dependencies
echo -e "\n${CYAN}[3/6]${NC} Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install -r requirements-dev.txt -q
echo -e "  ${GREEN}All packages installed${NC}"

# Copy .env
echo -e "\n${CYAN}[4/6]${NC} Setting up environment file..."
if [ ! -f ".env" ]; then
    cp .env.example .env
    echo -e "  ${GREEN}Created .env from template${NC}"
    echo -e "  ${YELLOW}→ Edit .env with your API keys${NC}"
else
    echo -e "  ${YELLOW}Already exists, skipping${NC}"
fi

# Create data directories
echo -e "\n${CYAN}[5/6]${NC} Creating data directories..."
mkdir -p data/raw data/processed data/output
touch data/raw/.gitkeep data/processed/.gitkeep data/output/.gitkeep
echo -e "  ${GREEN}Done${NC}"

# Run tests
echo -e "\n${CYAN}[6/6]${NC} Running tests..."
python -m pytest tests/ -v --tb=short 2>&1 || true

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Setup complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo "Next steps:"
echo "  1. Edit .env with your API keys:"
echo "     → Blockfrost: https://blockfrost.io (free Cardano API)"
echo "     → Supabase:   https://supabase.com  (free PostgreSQL)"
echo "     → MongoDB:    https://mongodb.com/atlas (free cluster)"
echo "     → Sentry:     https://sentry.io (free error tracking)"
echo ""
echo "  2. Activate the environment:"
echo "     source .venv/bin/activate"
echo ""
echo "  3. Start Jupyter:"
echo "     jupyter lab"
echo ""
echo "  4. Verify API connections:"
echo "     python scripts/check_services.py"
echo ""
