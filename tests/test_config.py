"""Tests for configuration loading."""

import os
from unittest.mock import patch

from src.utils.config import Settings


def test_settings_defaults():
    """Settings should have sensible defaults when no env vars are set."""
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings()
        assert settings.blockfrost_network == "testnet"
        assert settings.python_env == "development"
        assert settings.log_level == "INFO"
        assert settings.is_development is True
        assert settings.is_production is False


def test_settings_from_env():
    """Settings should read from environment variables."""
    with patch.dict(os.environ, {
        "BLOCKFROST_PROJECT_ID": "test_key_123",
        "BLOCKFROST_NETWORK": "mainnet",
        "PYTHON_ENV": "production",
    }):
        settings = Settings()
        assert settings.blockfrost_project_id == "test_key_123"
        assert settings.blockfrost_network == "mainnet"
        assert settings.is_production is True


def test_validate_missing_keys():
    """Validate should report missing required keys."""
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings()
        missing = settings.validate()
        assert "BLOCKFROST_PROJECT_ID" in missing


def test_validate_all_set():
    """Validate should return empty list when all keys are set."""
    with patch.dict(os.environ, {
        "BLOCKFROST_PROJECT_ID": "test_key",
    }):
        settings = Settings()
        missing = settings.validate()
        assert "BLOCKFROST_PROJECT_ID" not in missing
