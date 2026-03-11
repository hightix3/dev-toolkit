"""
Transaction helpers for Cardano using PyCardano + Blockfrost backend.

Usage:
    from src.blockchain.transactions import TransactionBuilder

    builder = TransactionBuilder()
    tx_hash = builder.send_ada(
        from_address="addr_test1...",
        to_address="addr_test1...",
        amount_lovelace=5_000_000,  # 5 ADA
        signing_key=my_skey,
    )
"""

from pycardano import (
    Address,
    BlockFrostChainContext,
    PaymentSigningKey,
    Transaction,
    TransactionBuilder as PyCardanoTxBuilder,
    TransactionOutput,
)

from src.utils import get_logger, get_settings

logger = get_logger(__name__)


class TransactionBuilder:
    """Build and submit Cardano transactions via Blockfrost."""

    NETWORK_IDS = {
        "mainnet": 1,
        "testnet": 0,
        "preview": 0,
        "preprod": 0,
    }

    def __init__(self):
        settings = get_settings()
        self.network = settings.blockfrost_network

        # Build the Blockfrost base URL
        base = f"https://cardano-{self.network}.blockfrost.io/api"
        self.context = BlockFrostChainContext(
            project_id=settings.blockfrost_project_id,
            base_url=base,
        )
        logger.info(f"Transaction builder ready ({self.network})")

    def send_ada(
        self,
        from_address: str,
        to_address: str,
        amount_lovelace: int,
        signing_key: PaymentSigningKey,
    ) -> str:
        """
        Send ADA from one address to another.

        Args:
            from_address: Sender's bech32 address
            to_address: Recipient's bech32 address
            amount_lovelace: Amount in lovelace (1 ADA = 1,000,000 lovelace)
            signing_key: Sender's signing key

        Returns:
            Transaction hash
        """
        builder = PyCardanoTxBuilder(self.context)
        builder.add_input_address(Address.from_primitive(from_address))
        builder.add_output(
            TransactionOutput(
                Address.from_primitive(to_address),
                amount_lovelace,
            )
        )

        signed_tx: Transaction = builder.build_and_sign(
            signing_keys=[signing_key],
            change_address=Address.from_primitive(from_address),
        )

        tx_hash = self.context.submit_tx(signed_tx.to_cbor())
        logger.info(f"Transaction submitted: {tx_hash}")
        return str(tx_hash)

    def estimate_fee(
        self,
        from_address: str,
        to_address: str,
        amount_lovelace: int,
    ) -> int:
        """Estimate transaction fee in lovelace."""
        builder = PyCardanoTxBuilder(self.context)
        builder.add_input_address(Address.from_primitive(from_address))
        builder.add_output(
            TransactionOutput(
                Address.from_primitive(to_address),
                amount_lovelace,
            )
        )
        # Build without signing to estimate fee
        tx_body = builder.build(
            change_address=Address.from_primitive(from_address),
        )
        return int(tx_body.fee)
