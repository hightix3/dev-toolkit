"""Tests for data analysis module."""

import numpy as np
import pandas as pd
import pytest


@pytest.fixture
def sample_df():
    """Create a sample DataFrame for testing."""
    np.random.seed(42)
    return pd.DataFrame({
        "id": range(100),
        "value": np.random.randn(100) * 10 + 50,
        "category": np.random.choice(["A", "B", "C"], 100),
        "amount": np.random.exponential(100, 100),
    })


def test_analyzer_summary(sample_df):
    """Analyzer should produce a summary dict."""
    from src.data_analysis.analyzer import DataAnalyzer

    analyzer = DataAnalyzer(sample_df)
    summary = analyzer.summary()

    assert summary["shape"] == (100, 4)
    assert "missing" in summary
    assert "numeric_stats" in summary
    assert summary["memory_mb"] > 0


def test_analyzer_correlation(sample_df):
    """Correlation matrix should be square and have correct columns."""
    from src.data_analysis.analyzer import DataAnalyzer

    analyzer = DataAnalyzer(sample_df)
    corr = analyzer.correlation_matrix()

    assert corr.shape[0] == corr.shape[1]
    assert "value" in corr.columns
    assert "amount" in corr.columns


def test_analyzer_outliers(sample_df):
    """Outlier detection should return a DataFrame."""
    from src.data_analysis.analyzer import DataAnalyzer

    analyzer = DataAnalyzer(sample_df)
    outliers = analyzer.detect_outliers("value", method="iqr")
    assert isinstance(outliers, pd.DataFrame)

    outliers_z = analyzer.detect_outliers("value", method="zscore")
    assert isinstance(outliers_z, pd.DataFrame)


def test_analyzer_missing_report():
    """Missing report should detect null values."""
    from src.data_analysis.analyzer import DataAnalyzer

    df = pd.DataFrame({
        "a": [1, 2, None, 4],
        "b": [None, None, 3, 4],
        "c": [1, 2, 3, 4],
    })
    analyzer = DataAnalyzer(df)
    report = analyzer.missing_report()

    assert len(report) == 2  # columns a and b have missing
    assert "b" in report.index
    assert report.loc["b", "missing_count"] == 2


def test_loader_save_processed(sample_df, tmp_path):
    """DataLoader should save processed files."""
    from src.data_analysis.loader import DataLoader
    from unittest.mock import patch, MagicMock
    from pathlib import Path

    settings = MagicMock()
    settings.data_dir = Path(tmp_path)

    with patch("src.data_analysis.loader.get_settings", return_value=settings):
        loader = DataLoader()
        path = loader.save_processed(sample_df, "test_output", fmt="csv")
        assert path.exists()
        assert path.suffix == ".csv"
