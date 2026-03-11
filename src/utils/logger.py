"""
Logging configuration with rich formatting.
"""

import logging
import sys

from rich.logging import RichHandler

from .config import get_settings


def get_logger(name: str = "dev-toolkit") -> logging.Logger:
    """Get a configured logger with rich formatting."""
    settings = get_settings()

    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = RichHandler(
            rich_tracebacks=True,
            show_time=True,
            show_path=False,
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        # Also log to file in development
        if settings.is_development:
            file_handler = logging.FileHandler("dev-toolkit.log")
            file_handler.setFormatter(
                logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            )
            logger.addHandler(file_handler)

    logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))
    return logger
