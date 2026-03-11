"""
Wallet and address utilities using PyCardano.

Usage:
    from src.blockchain import WalletManager

    wallet = WalletManager()
    address = wallet.generate_address()
    wallet.save_keys("my_wallet")
"""

from pathlib import Path

from pycardano import Address, Network, PaymentSigningKey, PaymentVerificationKey

from src.utils import get_logger, get_settings

logger = get_logger(__name__)


class WalletManager:
    """Manage Cardano wallets, keys, and addresses."""

    NETWORK_MAP = {
        "mainnet": Network.MAINNET,
        "testnet": Network.TESTNET,
        "preview": Network.TESTNET,
        "preprod": Network.TESTNET,
    }

    def __init__(self, network: str | None = None):
        settings = get_settings()
        net_name = network or settings.blockfrost_network
        self.network = self.NETWORK_MAP.get(net_name, Network.TESTNET)
        self._signing_key: PaymentSigningKey | None = None
        self._verification_key: PaymentVerificationKey | None = None
        self._address: Address | None = None
        logger.info(f"Wallet manager initialized ({net_name})")

    def generate_address(self) -> str:
        """Generate a new payment address with fresh keys."""
        self._signing_key = PaymentSigningKey.generate()
        self._verification_key = PaymentVerificationKey.from_signing_key(self._signing_key)
        self._address = Address(
            payment_part=self._verification_key.hash(), network=self.network
        )
        logger.info(f"Generated new address: {self._address}")
        return str(self._address)

    def save_keys(self, name: str, directory: str = "./keys") -> dict[str, str]:
        """
        Save signing and verification keys to files.

        WARNING: Keep .skey files secure — they control your funds.
        The .gitignore is configured to exclude key files.
        """
        if self._signing_key is None or self._verification_key is None:
            raise ValueError("No keys generated yet. Call generate_address() first.")

        key_dir = Path(directory)
        key_dir.mkdir(parents=True, exist_ok=True)

        skey_path = key_dir / f"{name}.skey"
        vkey_path = key_dir / f"{name}.vkey"
        addr_path = key_dir / f"{name}.addr"

        self._signing_key.save(str(skey_path))
        self._verification_key.save(str(vkey_path))
        addr_path.write_text(str(self._address))

        logger.info(f"Keys saved to {key_dir}/")
        return {
            "signing_key": str(skey_path),
            "verification_key": str(vkey_path),
            "address": str(addr_path),
        }

    def load_keys(self, name: str, directory: str = "./keys") -> str:
        """Load existing keys from files and return the address."""
        key_dir = Path(directory)

        skey_path = key_dir / f"{name}.skey"
        vkey_path = key_dir / f"{name}.vkey"

        if not skey_path.exists() or not vkey_path.exists():
            raise FileNotFoundError(f"Key files not found in {key_dir} for '{name}'")

        self._signing_key = PaymentSigningKey.load(str(skey_path))
        self._verification_key = PaymentVerificationKey.load(str(vkey_path))
        self._address = Address(
            payment_part=self._verification_key.hash(), network=self.network
        )

        logger.info(f"Loaded wallet: {self._address}")
        return str(self._address)

    @property
    def address(self) -> str | None:
        return str(self._address) if self._address else None
