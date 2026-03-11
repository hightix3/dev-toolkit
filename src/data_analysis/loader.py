"""
Data loading utilities — CSV, JSON, Excel, and database sources.

Usage:
    from src.data_analysis import DataLoader

    loader = DataLoader()
    df = loader.load_csv("data/raw/dataset.csv")
    df = loader.load_from_url("https://example.com/data.csv")
"""

from pathlib import Path

import pandas as pd

from src.utils import get_logger, get_settings

logger = get_logger(__name__)


class DataLoader:
    """Load data from various sources into Pandas DataFrames."""

    def __init__(self):
        settings = get_settings()
        self.data_dir = settings.data_dir

    def load_csv(self, path: str, **kwargs) -> pd.DataFrame:
        """Load a CSV file."""
        filepath = Path(path)
        logger.info(f"Loading CSV: {filepath}")
        df = pd.read_csv(filepath, **kwargs)
        logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")
        return df

    def load_excel(self, path: str, **kwargs) -> pd.DataFrame:
        """Load an Excel file."""
        filepath = Path(path)
        logger.info(f"Loading Excel: {filepath}")
        df = pd.read_excel(filepath, **kwargs)
        logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")
        return df

    def load_json(self, path: str, **kwargs) -> pd.DataFrame:
        """Load a JSON file."""
        filepath = Path(path)
        logger.info(f"Loading JSON: {filepath}")
        df = pd.read_json(filepath, **kwargs)
        logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")
        return df

    def load_from_url(self, url: str, file_type: str = "csv", **kwargs) -> pd.DataFrame:
        """Load data from a URL."""
        logger.info(f"Loading from URL: {url}")
        if file_type == "csv":
            df = pd.read_csv(url, **kwargs)
        elif file_type == "json":
            df = pd.read_json(url, **kwargs)
        elif file_type == "excel":
            df = pd.read_excel(url, **kwargs)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
        logger.info(f"Loaded {len(df)} rows, {len(df.columns)} columns")
        return df

    def save_processed(self, df: pd.DataFrame, filename: str, fmt: str = "csv") -> Path:
        """Save a processed DataFrame to the processed data directory."""
        output_dir = self.data_dir / "processed"
        output_dir.mkdir(parents=True, exist_ok=True)

        if fmt == "csv":
            path = output_dir / f"{filename}.csv"
            df.to_csv(path, index=False)
        elif fmt == "parquet":
            path = output_dir / f"{filename}.parquet"
            df.to_parquet(path, index=False)
        elif fmt == "json":
            path = output_dir / f"{filename}.json"
            df.to_json(path, orient="records", indent=2)
        else:
            raise ValueError(f"Unsupported format: {fmt}")

        logger.info(f"Saved to {path}")
        return path
