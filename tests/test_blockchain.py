"""Tests for blockchain module."""

import os
from unittest.mock import MagicMock, patch

import pytest


def test_cardano_client_requires_api_key():
    """Client should raise ValueError when no API key is provided."""
    with patch.dict(os.environ, {"BLOCKFROST_PROJECT_ID": ""}, clear=False):
        from src.utils.config import Settings

        with patch("src.blockchain.client.get_settings") as mock_settings:
            mock_settings.return_value = Settings()
            mock_settings.return_value.blockfrost_project_id = ""

            from src.blockchain.client import CardanoClient
            with pytest.raises(ValueError, match="Blockfrost API key not set"):
                CardanoClient(project_id="")


def test_wallet_generate_address():
    """WalletManager should generate a valid testnet address."""
    from src.blockchain.wallet import WalletManager

    wallet = WalletManager(network="testnet")
    address = wallet.generate_address()

    assert address is not None
    assert address.startswith("addr_test1")
    assert len(address) > 50


def test_wallet_save_and_load(tmp_path):
    """WalletManager should save and load keys correctly."""
    from src.blockchain.wallet import WalletManager

    # Generate
    wallet = WalletManager(network="testnet")
    address1 = wallet.generate_address()
    wallet.save_keys("test_wallet", directory=str(tmp_path))

    # Verify files exist
    assert (tmp_path / "test_wallet.skey").exists()
    assert (tmp_path / "test_wallet.vkey").exists()
    assert (tmp_path / "test_wallet.addr").exists()

    # Load
    wallet2 = WalletManager(network="testnet")
    address2 = wallet2.load_keys("test_wallet", directory=str(tmp_path))
    assert address1 == address2
