"""Plain-language, ranked insights generated from a profiled DuckDB table."""

from insights_forge.insights.engine import generate_insights
from insights_forge.insights.models import (
    Aggregation,
    Granularity,
    Insight,
    InsightKind,
    InsightReport,
    Roles,
    TrendSeries,
)
from insights_forge.insights.roles import (
    candidate_dates,
    candidate_dimensions,
    candidate_measures,
    detect_roles,
)

__all__ = [
    "Aggregation",
    "Granularity",
    "Insight",
    "InsightKind",
    "InsightReport",
    "Roles",
    "TrendSeries",
    "candidate_dates",
    "candidate_dimensions",
    "candidate_measures",
    "detect_roles",
    "generate_insights",
]
