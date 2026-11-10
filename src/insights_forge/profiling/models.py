"""Immutable result objects produced by the profiler.

They are plain frozen dataclasses: cheap, picklable (Streamlit caching), easy to
serialize later for the HTML/JSON report, and fully typed.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import StrEnum


class ColumnKind(StrEnum):
    NUMERIC = "numeric"
    TEXT = "text"
    TEMPORAL = "temporal"
    BOOLEAN = "boolean"
    OTHER = "other"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


@dataclass(frozen=True, slots=True)
class ValueCount:
    value: str
    count: int


@dataclass(frozen=True, slots=True)
class NumericStats:
    min: float
    max: float
    mean: float
    std: float | None  # undefined for a single value
    q1: float
    median: float
    q3: float
    zeros: int
    negatives: int
    lower_fence: float
    upper_fence: float
    outliers_low: int
    outliers_high: int

    @property
    def iqr(self) -> float:
        return self.q3 - self.q1

    @property
    def outliers(self) -> int:
        return self.outliers_low + self.outliers_high


@dataclass(frozen=True, slots=True)
class TextStats:
    min_length: int
    max_length: int
    mean_length: float
    blank: int  # empty or whitespace-only
    padded: int  # leading/trailing whitespace around real content
    numeric_like: int  # values that parse as numbers
    date_like: int  # values that parse as dates


@dataclass(frozen=True, slots=True)
class TemporalStats:
    min: dt.date
    max: dt.date
    span_days: int


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    name: str
    dtype: str
    kind: ColumnKind
    count: int  # non-null values
    nulls: int
    distinct: int
    numeric: NumericStats | None = None
    text: TextStats | None = None
    temporal: TemporalStats | None = None
    top_values: tuple[ValueCount, ...] = ()

    @property
    def rows(self) -> int:
        return self.count + self.nulls

    @property
    def null_pct(self) -> float:
        return 100 * self.nulls / self.rows if self.rows else 0.0

    @property
    def distinct_pct(self) -> float:
        return 100 * self.distinct / self.count if self.count else 0.0


@dataclass(frozen=True, slots=True)
class Correlation:
    column_a: str
    column_b: str
    pearson: float

    @property
    def strength(self) -> str:
        r = abs(self.pearson)
        if r >= 0.7:
            return "strong"
        if r >= 0.4:
            return "moderate"
        if r >= 0.2:
            return "weak"
        return "negligible"


@dataclass(frozen=True, slots=True)
class Issue:
    check: str
    severity: Severity
    message: str
    column: str | None = None
    affected_rows: int = 0


@dataclass(frozen=True, slots=True)
class DatasetProfile:
    table: str
    rows: int
    columns: tuple[ColumnProfile, ...]
    duplicate_rows: int  # extra copies beyond the first occurrence
    correlations: tuple[Correlation, ...] = ()
    issues: tuple[Issue, ...] = field(default=())

    @property
    def n_columns(self) -> int:
        return len(self.columns)

    @property
    def total_cells(self) -> int:
        return self.rows * self.n_columns

    @property
    def missing_cells(self) -> int:
        return sum(c.nulls for c in self.columns)

    @property
    def completeness_pct(self) -> float:
        return 100 * (1 - self.missing_cells / self.total_cells) if self.total_cells else 100.0

    def column(self, name: str) -> ColumnProfile:
        for col in self.columns:
            if col.name == name:
                return col
        raise KeyError(name)

    def columns_of(self, kind: ColumnKind) -> tuple[ColumnProfile, ...]:
        return tuple(c for c in self.columns if c.kind is kind)

    def issues_by_severity(self, severity: Severity) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is severity)
