"""
Data visualization utilities — charts saved to data/output/.

Usage:
    from src.data_analysis import DataVisualizer

    viz = DataVisualizer(df)
    viz.histogram("age", title="Age Distribution")
    viz.correlation_heatmap()
    viz.time_series("date", "price", title="Price Over Time")
"""

from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib
import numpy as np
import pandas as pd
import seaborn as sns

from src.utils import get_logger, get_settings

matplotlib.use("Agg")  # Non-interactive backend for saving

logger = get_logger(__name__)

# Consistent style
sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams.update({
    "figure.figsize": (10, 6),
    "figure.dpi": 150,
    "font.size": 11,
    "axes.titlesize": 14,
    "axes.labelsize": 12,
})


class DataVisualizer:
    """Create and save data visualizations."""

    def __init__(self, df: pd.DataFrame):
        self.df = df
        settings = get_settings()
        self.output_dir = settings.data_dir / "output"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _save(self, fig: plt.Figure, filename: str) -> Path:
        """Save a figure to the output directory."""
        path = self.output_dir / f"{filename}.png"
        fig.savefig(path, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        logger.info(f"Chart saved: {path}")
        return path

    def histogram(self, column: str, bins: int = 30, title: str | None = None) -> Path:
        """Create a histogram of a numeric column."""
        fig, ax = plt.subplots()
        self.df[column].hist(bins=bins, ax=ax, edgecolor="white", alpha=0.8)
        ax.set_title(title or f"Distribution of {column}")
        ax.set_xlabel(column)
        ax.set_ylabel("Frequency")
        return self._save(fig, f"hist_{column}")

    def scatter(
        self, x: str, y: str, hue: str | None = None, title: str | None = None
    ) -> Path:
        """Create a scatter plot."""
        fig, ax = plt.subplots()
        sns.scatterplot(data=self.df, x=x, y=y, hue=hue, ax=ax, alpha=0.7)
        ax.set_title(title or f"{y} vs {x}")
        return self._save(fig, f"scatter_{x}_{y}")

    def correlation_heatmap(self, title: str = "Correlation Matrix") -> Path:
        """Create a correlation heatmap for numeric columns."""
        numeric_df = self.df.select_dtypes(include=[np.number])
        corr = numeric_df.corr()

        fig, ax = plt.subplots(figsize=(max(8, len(corr.columns)), max(6, len(corr.columns) * 0.8)))
        sns.heatmap(
            corr, annot=True, fmt=".2f", cmap="coolwarm", center=0,
            square=True, ax=ax, linewidths=0.5
        )
        ax.set_title(title)
        return self._save(fig, "correlation_heatmap")

    def time_series(
        self, date_col: str, value_col: str, title: str | None = None
    ) -> Path:
        """Create a time series line chart."""
        fig, ax = plt.subplots()
        df_sorted = self.df.sort_values(date_col)
        ax.plot(df_sorted[date_col], df_sorted[value_col], linewidth=1.5)
        ax.set_title(title or f"{value_col} over time")
        ax.set_xlabel(date_col)
        ax.set_ylabel(value_col)
        fig.autofmt_xdate()
        return self._save(fig, f"timeseries_{value_col}")

    def bar_chart(
        self, column: str, top_n: int = 15, title: str | None = None
    ) -> Path:
        """Create a horizontal bar chart of value counts."""
        counts = self.df[column].value_counts().head(top_n)
        fig, ax = plt.subplots()
        counts.sort_values().plot(kind="barh", ax=ax, edgecolor="white")
        ax.set_title(title or f"Top {top_n} — {column}")
        ax.set_xlabel("Count")
        return self._save(fig, f"bar_{column}")

    def box_plot(self, column: str, by: str | None = None, title: str | None = None) -> Path:
        """Create a box plot for outlier visualization."""
        fig, ax = plt.subplots()
        if by:
            sns.boxplot(data=self.df, x=by, y=column, ax=ax)
        else:
            sns.boxplot(data=self.df, y=column, ax=ax)
        ax.set_title(title or f"Box Plot — {column}")
        return self._save(fig, f"box_{column}")
