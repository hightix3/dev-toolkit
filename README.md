# 🛠️ Free Dev Toolkit

A ready-to-use development environment for **Python**, **Cardano/Plutus blockchain**, and **data analysis** — built entirely on free-tier services.

## Quick Start

### Windows (PowerShell)
```powershell
.\scripts\setup.ps1
```

### Linux / macOS / WSL
```bash
chmod +x scripts/setup.sh
./scripts/setup.sh
```

### Manual Setup
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
# Edit .env with your API keys
```

## Project Structure

```
dev-toolkit/
├── .github/workflows/     # CI/CD pipelines
│   ├── python-ci.yml      # Lint, test, coverage on every push
│   └── blockchain-test.yml # Cardano integration tests
├── .vscode/               # VS Code settings & extensions
│   ├── settings.json
│   └── extensions.json
├── configs/               # Service configurations
│   └── jupyter_config.py
├── data/                  # Data analysis workspace
│   ├── raw/               # Raw input data
│   ├── processed/         # Cleaned/transformed data
│   └── output/            # Analysis results, charts
├── notebooks/             # Jupyter notebooks
│   ├── 01_data_exploration.ipynb
│   ├── 02_blockfrost_cardano.ipynb
│   └── template.ipynb
├── scripts/               # Setup & utility scripts
│   ├── setup.sh           # Linux/macOS setup
│   ├── setup.ps1          # Windows PowerShell setup
│   └── check_services.py  # Verify all API connections
├── src/                   # Source code
│   ├── utils/             # Shared utilities
│   │   ├── __init__.py
│   │   ├── config.py      # Environment config loader
│   │   └── logger.py      # Logging setup
│   ├── blockchain/        # Cardano/Blockfrost integration
│   │   ├── __init__.py
│   │   ├── client.py      # Blockfrost API client wrapper
│   │   ├── wallet.py      # Wallet & address utilities
│   │   └── transactions.py # Transaction helpers
│   └── data_analysis/     # Data analysis modules
│       ├── __init__.py
│       ├── loader.py      # Data loading utilities
│       ├── analyzer.py    # Analysis functions
│       └── visualizer.py  # Chart/plot generation
├── tests/                 # Test suite
│   ├── test_config.py
│   ├── test_blockchain.py
│   └── test_data.py
├── .env.example           # Template for environment variables
├── .gitignore
├── pyproject.toml         # Project metadata & tool config
├── requirements.txt       # Production dependencies
└── requirements-dev.txt   # Development dependencies
```

## What's Included

### 🐍 Python Development
- Project structure with `pyproject.toml` configuration
- Linting (ruff), formatting (black), type checking (mypy)
- Testing with pytest + coverage
- VS Code settings with recommended extensions

### ⛓️ Cardano / Blockchain
- Blockfrost API client wrapper (Python SDK)
- PyCardano integration for wallet & transaction operations
- Example notebook for querying the Cardano blockchain
- Testnet configuration by default (safe for development)

### 📊 Data Analysis
- Jupyter notebook templates with best-practice structure
- Data loading, cleaning, and visualization utilities
- Pandas, NumPy, Matplotlib, Seaborn pre-configured
- Organized data directory (raw → processed → output)

### 🚀 CI/CD
- GitHub Actions workflow: lint → test → coverage on every push
- Separate workflow for blockchain integration tests
- Automated dependency caching for fast builds

## Free Services Used

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| [GitHub](https://github.com) | Repos, CI/CD, Packages | Unlimited |
| [Blockfrost](https://blockfrost.io) | Cardano API | 50k req/day |
| [Google Colab](https://colab.google) | GPU notebooks | Free |
| [Supabase](https://supabase.com) | PostgreSQL database | 500MB |
| [MongoDB Atlas](https://www.mongodb.com/atlas) | NoSQL database | 512MB |
| [UptimeRobot](https://uptimerobot.com) | Monitoring | 50 monitors |
| [Sentry](https://sentry.io) | Error tracking | 5k errors/mo |
| [Cloudflare](https://cloudflare.com) | CDN, DNS, SSL | Unlimited |

## Configuration

Copy `.env.example` to `.env` and fill in your API keys:

```bash
cp .env.example .env
```

See each service's website to create a free account and obtain API keys.

## License

MIT
