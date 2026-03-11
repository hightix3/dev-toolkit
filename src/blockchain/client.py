"""
Blockfrost API client wrapper for Cardano blockchain queries.

Usage:
    from src.blockchain import CardanoClient

    client = CardanoClient()
    health = client.check_health()
    tip = client.get_latest_block()
    addr = client.get_address_info("addr1...")
"""

from blockfrost import ApiError, ApiUrls, BlockFrostApi

from src.utils import get_logger, get_settings

logger = get_logger(__name__)


class CardanoClient:
    """Wrapper around the Blockfrost Python SDK for Cardano queries."""

    NETWORK_URLS = {
        "mainnet": ApiUrls.mainnet.value,
        "testnet": ApiUrls.testnet.value,
        "preview": ApiUrls.preview.value,
        "preprod": ApiUrls.preprod.value,
    }

    def __init__(self, project_id: str | None = None, network: str | None = None):
        settings = get_settings()
        self.project_id = project_id or settings.blockfrost_project_id
        self.network = network or settings.blockfrost_network

        if not self.project_id:
            raise ValueError(
                "Blockfrost API key not set. "
                "Set BLOCKFROST_PROJECT_ID in .env or pass project_id parameter. "
                "Get a free key at https://blockfrost.io"
            )

        base_url = self.NETWORK_URLS.get(self.network, ApiUrls.testnet.value)
        self.api = BlockFrostApi(project_id=self.project_id, base_url=base_url)
        logger.info(f"Cardano client initialized on {self.network}")

    def check_health(self) -> bool:
        """Check if the Blockfrost API is healthy."""
        try:
            health = self.api.health()
            return health.is_healthy
        except ApiError as e:
            logger.error(f"Health check failed: {e}")
            return False

    def get_latest_block(self) -> dict:
        """Get the latest block on the chain."""
        try:
            block = self.api.block_latest()
            return {
                "hash": block.hash,
                "height": block.height,
                "slot": block.slot,
                "epoch": block.epoch,
                "time": block.time,
                "tx_count": block.tx_count,
            }
        except ApiError as e:
            logger.error(f"Failed to get latest block: {e}")
            raise

    def get_address_info(self, address: str) -> dict:
        """Get information about a Cardano address."""
        try:
            addr = self.api.address(address=address)
            return {
                "address": address,
                "type": addr.type,
                "balance": [
                    {"unit": a.unit, "quantity": a.quantity} for a in addr.amount
                ],
            }
        except ApiError as e:
            logger.error(f"Failed to get address info: {e}")
            raise

    def get_address_utxos(self, address: str) -> list[dict]:
        """Get UTXOs for an address."""
        try:
            utxos = self.api.address_utxos(address=address)
            return [
                {
                    "tx_hash": u.tx_hash,
                    "tx_index": u.tx_index,
                    "amount": [{"unit": a.unit, "quantity": a.quantity} for a in u.amount],
                }
                for u in utxos
            ]
        except ApiError as e:
            logger.error(f"Failed to get UTXOs: {e}")
            raise

    def get_transaction(self, tx_hash: str) -> dict:
        """Get transaction details by hash."""
        try:
            tx = self.api.transaction(hash=tx_hash)
            return {
                "hash": tx.hash,
                "block": tx.block,
                "block_height": tx.block_height,
                "slot": tx.slot,
                "index": tx.index,
                "fees": tx.fees,
                "size": tx.size,
            }
        except ApiError as e:
            logger.error(f"Failed to get transaction: {e}")
            raise

    def get_epoch_info(self, epoch: int | None = None) -> dict:
        """Get epoch information. Defaults to latest epoch."""
        try:
            if epoch is None:
                ep = self.api.epoch_latest()
            else:
                ep = self.api.epoch(number=epoch)
            return {
                "epoch": ep.epoch,
                "start_time": ep.start_time,
                "end_time": ep.end_time,
                "first_block_time": ep.first_block_time,
                "last_block_time": ep.last_block_time,
                "block_count": ep.block_count,
                "tx_count": ep.tx_count,
                "output": ep.output,
                "fees": ep.fees,
            }
        except ApiError as e:
            logger.error(f"Failed to get epoch info: {e}")
            raise

    def get_network_info(self) -> dict:
        """Get overall network information."""
        try:
            info = self.api.network()
            return {
                "supply_max": info.supply.max,
                "supply_total": info.supply.total,
                "supply_circulating": info.supply.circulating,
                "stake_live": info.stake.live,
                "stake_active": info.stake.active,
            }
        except ApiError as e:
            logger.error(f"Failed to get network info: {e}")
            raise
