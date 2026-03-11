# ===========================================
# Dev Toolkit Setup — Windows PowerShell
# ===========================================

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  Free Dev Toolkit — Environment Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# Check Python
Write-Host "[1/6] Checking Python..." -ForegroundColor Cyan
try {
    $pythonVersion = python --version 2>&1
    Write-Host "  Found $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "  ERROR: Python not found. Install Python 3.10+ first." -ForegroundColor Red
    Write-Host "  -> https://www.python.org/downloads/" -ForegroundColor Yellow
    exit 1
}

# Create virtual environment
Write-Host "`n[2/6] Creating virtual environment..." -ForegroundColor Cyan
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    Write-Host "  Created .venv" -ForegroundColor Green
} else {
    Write-Host "  Already exists, skipping" -ForegroundColor Yellow
}

# Activate
Write-Host "  Activating..." -ForegroundColor Cyan
& .\.venv\Scripts\Activate.ps1
Write-Host "  Activated" -ForegroundColor Green

# Install dependencies
Write-Host "`n[3/6] Installing dependencies..." -ForegroundColor Cyan
pip install --upgrade pip -q
pip install -r requirements.txt -q
pip install -r requirements-dev.txt -q
Write-Host "  All packages installed" -ForegroundColor Green

# Copy .env
Write-Host "`n[4/6] Setting up environment file..." -ForegroundColor Cyan
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "  Created .env from template" -ForegroundColor Green
    Write-Host "  -> Edit .env with your API keys" -ForegroundColor Yellow
} else {
    Write-Host "  Already exists, skipping" -ForegroundColor Yellow
}

# Create data directories
Write-Host "`n[5/6] Creating data directories..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path data/raw, data/processed, data/output | Out-Null
@("data/raw/.gitkeep", "data/processed/.gitkeep", "data/output/.gitkeep") | ForEach-Object {
    if (-not (Test-Path $_)) { New-Item -ItemType File -Path $_ -Force | Out-Null }
}
Write-Host "  Done" -ForegroundColor Green

# Run tests
Write-Host "`n[6/6] Running tests..." -ForegroundColor Cyan
python -m pytest tests/ -v --tb=short

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Setup complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Edit .env with your API keys:"
Write-Host "     -> Blockfrost: https://blockfrost.io (free Cardano API)" -ForegroundColor Yellow
Write-Host "     -> Supabase:   https://supabase.com  (free PostgreSQL)" -ForegroundColor Yellow
Write-Host "     -> MongoDB:    https://mongodb.com/atlas (free cluster)" -ForegroundColor Yellow
Write-Host "     -> Sentry:     https://sentry.io (free error tracking)" -ForegroundColor Yellow
Write-Host ""
Write-Host "  2. Activate the environment:"
Write-Host "     .\.venv\Scripts\Activate.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "  3. Start Jupyter:"
Write-Host "     jupyter lab" -ForegroundColor Cyan
Write-Host ""
Write-Host "  4. Verify API connections:"
Write-Host "     python scripts\check_services.py" -ForegroundColor Cyan
Write-Host ""
