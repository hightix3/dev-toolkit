"""
Environment configuration loader.
Reads from .env file and provides typed access to all settings.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).parent.parent.parent
load_dotenv(_project_root / ".env")


@dataclass
class Settings:
    """Application settings loaded from environment variables."""

    # Blockfrost / Cardano
    blockfrost_project_id: str = field(
        default_factory=lambda: os.getenv("BLOCKFROST_PROJECT_ID", "")
    )
    blockfrost_network: str = field(
        default_factory=lambda: os.getenv("BLOCKFROST_NETWORK", "testnet")
    )

    # Supabase
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))
    supabase_anon_key: str = field(default_factory=lambda: os.getenv("SUPABASE_ANON_KEY", ""))

    # MongoDB
    mongodb_uri: str = field(default_factory=lambda: os.getenv("MONGODB_URI", ""))

    # Sentry
    sentry_dsn: str = field(default_factory=lambda: os.getenv("SENTRY_DSN", ""))

    # General
    python_env: str = field(default_factory=lambda: os.getenv("PYTHON_ENV", "development"))
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    data_dir: Path = field(
        default_factory=lambda: Path(os.getenv("DATA_DIR", "./data"))
    )

    @property
    def is_production(self) -> bool:
        return self.python_env == "production"

    @property
    def is_development(self) -> bool:
        return self.python_env == "development"

    def validate(self) -> list[str]:
        """Check which required settings are missing. Returns list of missing keys."""
        missing = []
        if not self.blockfrost_project_id:
            missing.append("BLOCKFROST_PROJECT_ID")
        return missing


# Singleton
_settings: Settings | None = None


def get_settings() -> Settings:
    """Get application settings (singleton)."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
