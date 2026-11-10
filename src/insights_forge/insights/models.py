"""Result objects for the insight engine."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import StrEnum


class InsightKind(StrEnum):
    TREND = "trend"
    PEAK = "peak"
    CONCENTRATION = "concentration"
    SEGMENT = "segment"
    CORRELATION = "correlation"
    DISTRIBUTION = "distribution"
    QUALITY = "quality"


class Aggregation(StrEnum):
    SUM = "sum"
    AVG = "avg"
    COUNT = "count"


class Granularity(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    QUARTER = "quarter"

    def label(self, period: dt.date) -> str:
        match self:
            case Granularity.DAY | Granularity.WEEK:
                return period.isoformat()
            case Granularity.MONTH:
                return period.strftime("%b %Y")
            case Granularity.QUARTER:
                return f"Q{(period.month - 1) // 3 + 1} {period.year}"


@dataclass(frozen=True, slots=True)
class Roles:
    """Which columns play which part in the analysis."""

    measure: str | None  # None -> count rows
    aggregation: Aggregation
    date: str | None
    dimensions: tuple[str, ...]

    @property
    def measure_label(self) -> str:
        if self.measure is None:
            return "row count"
        return f"{'average ' if self.aggregation is Aggregation.AVG else ''}{self.measure}"


@dataclass(frozen=True, slots=True)
class Insight:
    kind: InsightKind
    title: str  # one-line headline
    detail: str  # supporting sentence with the numbers
    score: float  # 0..1, higher = more noteworthy
    columns: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TrendSeries:
    granularity: Granularity
    periods: tuple[dt.date, ...]
    values: tuple[float, ...]
    slope_pct: float | None  # fitted change per period, % of the mean
    r2: float | None  # goodness of the linear fit

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(self.granularity.label(p) for p in self.periods)


@dataclass(frozen=True, slots=True)
class InsightReport:
    roles: Roles
    insights: tuple[Insight, ...]
    trend: TrendSeries | None = None
