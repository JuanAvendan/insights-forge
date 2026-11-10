"""Column profiling and data-quality checks on DuckDB tables."""

from insights_forge.profiling.config import ProfileConfig
from insights_forge.profiling.models import (
    ColumnKind,
    ColumnProfile,
    Correlation,
    DatasetProfile,
    Issue,
    NumericStats,
    Severity,
    TemporalStats,
    TextStats,
    ValueCount,
)
from insights_forge.profiling.profiler import profile_table

__all__ = [
    "ColumnKind",
    "ColumnProfile",
    "Correlation",
    "DatasetProfile",
    "Issue",
    "NumericStats",
    "ProfileConfig",
    "Severity",
    "TemporalStats",
    "TextStats",
    "ValueCount",
    "profile_table",
]
