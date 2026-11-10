"""Tunable thresholds for profiling and data-quality checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileConfig:
    top_k: int = 5
    """Most frequent values kept for text/boolean columns."""

    iqr_factor: float = 3.0
    """Tukey fence multiplier. 1.5 flags "mild" outliers; 3.0 only "extreme" ones,
    which keeps automated reports quiet on naturally skewed data."""

    max_outlier_pct: float = 5.0
    """If more than this % of values fall outside the fences, the column is
    heavy-tailed rather than containing outliers, so no outlier issue is raised."""

    missing_warning_pct: float = 5.0
    missing_critical_pct: float = 50.0

    suspicious_negative_pct: float = 5.0
    """Negatives are flagged only when they are a small minority (likely errors)."""

    max_correlation_columns: int = 30
    """Caps the number of numeric columns in the correlation matrix (n² pairs)."""
