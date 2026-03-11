"""
Data analysis functions — summary stats, correlations, outlier detection.

Usage:
    from src.data_analysis import DataAnalyzer

    analyzer = DataAnalyzer(df)
    summary = analyzer.summary()
    outliers = analyzer.detect_outliers("column_name")
"""

import pandas as pd
import numpy as np
from scipy import stats

from src.utils import get_logger

logger = get_logger(__name__)


class DataAnalyzer:
    """Perform common data analysis operations on a DataFrame."""

    def __init__(self, df: pd.DataFrame):
        self.df = df

    def summary(self) -> dict:
        """Get a comprehensive summary of the dataset."""
        return {
            "shape": self.df.shape,
            "dtypes": self.df.dtypes.to_dict(),
            "missing": self.df.isnull().sum().to_dict(),
            "missing_pct": (self.df.isnull().sum() / len(self.df) * 100).to_dict(),
            "numeric_stats": self.df.describe().to_dict(),
            "memory_mb": self.df.memory_usage(deep=True).sum() / 1024 / 1024,
        }

    def correlation_matrix(self, method: str = "pearson") -> pd.DataFrame:
        """Compute correlation matrix for numeric columns."""
        numeric_df = self.df.select_dtypes(include=[np.number])
        corr = numeric_df.corr(method=method)
        logger.info(f"Computed {method} correlation matrix ({corr.shape})")
        return corr

    def detect_outliers(self, column: str, method: str = "iqr") -> pd.DataFrame:
        """
        Detect outliers in a numeric column.

        Methods:
            iqr: Interquartile range (Q1 - 1.5*IQR to Q3 + 1.5*IQR)
            zscore: Z-score > 3
        """
        series = self.df[column].dropna()

        if method == "iqr":
            q1 = series.quantile(0.25)
            q3 = series.quantile(0.75)
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            mask = (self.df[column] < lower) | (self.df[column] > upper)
        elif method == "zscore":
            z_scores = np.abs(stats.zscore(series))
            mask = self.df.index.isin(series.index[z_scores > 3])
        else:
            raise ValueError(f"Unknown method: {method}")

        outliers = self.df[mask]
        logger.info(f"Found {len(outliers)} outliers in '{column}' using {method}")
        return outliers

    def value_counts_summary(self, column: str, top_n: int = 10) -> pd.DataFrame:
        """Get value counts with percentages for a categorical column."""
        counts = self.df[column].value_counts().head(top_n)
        pcts = self.df[column].value_counts(normalize=True).head(top_n) * 100
        return pd.DataFrame({"count": counts, "percentage": pcts.round(2)})

    def missing_report(self) -> pd.DataFrame:
        """Generate a report of missing values by column."""
        missing = self.df.isnull().sum()
        missing_pct = (missing / len(self.df) * 100).round(2)
        report = pd.DataFrame({
            "missing_count": missing,
            "missing_pct": missing_pct,
            "dtype": self.df.dtypes,
        })
        return report[report["missing_count"] > 0].sort_values("missing_pct", ascending=False)
