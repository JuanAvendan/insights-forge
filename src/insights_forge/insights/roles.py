"""Guess the analytical role of each column: measure, date and dimensions.

Heuristics are name-based (English and Spanish) plus profile statistics. The
app and CLI let the user override any choice.
"""

from __future__ import annotations

import re

from insights_forge.insights.models import Aggregation, Roles
from insights_forge.profiling import ColumnKind, ColumnProfile, DatasetProfile
from insights_forge.profiling.quality import looks_like_identifier

# Additive quantities: summing them is meaningful.
_SUM_HINT = re.compile(
    r"total|amount|revenue|sales|income|profit|cost|spend|value|qty|quantity|units|"
    r"monto|importe|ventas|ingreso|ganancia|costo|valor|cantidad",
    re.IGNORECASE,
)
# Intensive quantities: only an average makes sense.
_AVG_HINT = re.compile(
    r"price|rate|ratio|pct|percent|score|age|avg|mean|discount|temperature|"
    r"precio|tasa|porcentaje|puntaje|edad|promedio|descuento",
    re.IGNORECASE,
)
# Strongest sum hints first: "total" beats "quantity" beats a generic number.
_PRIORITY = ("total", "revenue", "sales", "amount", "monto", "ventas", "importe", "valor", "value")

MAX_DIMENSION_CARDINALITY = 50


def aggregation_for(name: str) -> Aggregation:
    """Sum additive quantities, average intensive ones (prices, rates, scores...)."""
    if _AVG_HINT.search(name) and not _SUM_HINT.search(name):
        return Aggregation.AVG
    return Aggregation.SUM


def _measure_rank(col: ColumnProfile) -> tuple[int, int, int]:
    name = col.name.lower()
    priority = next((len(_PRIORITY) - i for i, p in enumerate(_PRIORITY) if p in name), 0)
    return (priority, int(bool(_SUM_HINT.search(name))), col.distinct)


def candidate_measures(profile: DatasetProfile) -> tuple[str, ...]:
    """Numeric, non-identifier columns that vary, best candidates first."""
    cols = [
        c
        for c in profile.columns_of(ColumnKind.NUMERIC)
        if c.distinct > 2 and not looks_like_identifier(c.name)
    ]
    return tuple(c.name for c in sorted(cols, key=_measure_rank, reverse=True))


def candidate_dates(profile: DatasetProfile) -> tuple[str, ...]:
    cols = [
        c
        for c in profile.columns_of(ColumnKind.TEMPORAL)
        if c.temporal and c.temporal.span_days > 0
    ]
    return tuple(c.name for c in sorted(cols, key=lambda c: c.distinct, reverse=True))


def candidate_dimensions(profile: DatasetProfile) -> tuple[str, ...]:
    """Low-cardinality categorical columns, good for group-by comparisons."""
    return tuple(
        c.name
        for c in profile.columns
        if c.kind in {ColumnKind.TEXT, ColumnKind.BOOLEAN}
        and 2 <= c.distinct <= MAX_DIMENSION_CARDINALITY
        and not looks_like_identifier(c.name)
    )


def detect_roles(
    profile: DatasetProfile,
    measure: str | None = None,
    date: str | None = None,
) -> Roles:
    """Pick roles automatically; explicit ``measure``/``date`` arguments win."""
    if measure is None:
        measures = candidate_measures(profile)
        measure = measures[0] if measures else None
    if date is None:
        dates = candidate_dates(profile)
        date = dates[0] if dates else None
    return Roles(
        measure=measure,
        aggregation=aggregation_for(measure) if measure else Aggregation.COUNT,
        date=date,
        dimensions=candidate_dimensions(profile),
    )
