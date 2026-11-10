"""Guess the analytical role of each column: measure, date and dimensions.

Heuristics work on the *words* of a column name (``box_office_usd`` ->
``box, office, usd``; ``unitPrice`` -> ``unit, price``), in English and Spanish,
plus profile statistics. The app and CLI let the user override every choice.
"""

from __future__ import annotations

import re
import unicodedata

from insights_forge.insights.models import Aggregation, Roles
from insights_forge.profiling import ColumnKind, ColumnProfile, DatasetProfile
from insights_forge.profiling.quality import looks_like_identifier

# Money and other clearly additive amounts, most preferred first.
STRONG_SUM = (
    "total", "revenue", "sales", "gross", "box", "income", "profit", "amount", "budget",
    "cost", "costs", "spend", "usd", "eur",
    "ventas", "venta", "monto", "importe", "ingreso", "ingresos", "taquilla", "ganancia",
    "presupuesto", "costo",
)  # fmt: skip
# Additive but generic: fine to sum, weaker evidence of being "the" measure.
WEAK_SUM = frozenset(
    {"value", "valor", "qty", "quantity", "units", "cantidad", "count", "orders", "pedidos"}
)
# Intensive quantities: only an average makes sense.
AVG_WORDS = frozenset(
    {
        "price", "rate", "ratio", "pct", "percent", "percentage", "score", "rating", "age",
        "avg", "mean", "average", "discount", "temperature", "runtime", "duration", "minutes",
        "min", "precio", "tasa", "porcentaje", "puntaje", "calificacion", "edad", "promedio",
        "descuento", "duracion",
    }
)  # fmt: skip

# Calendar parts stored as numbers (release_year, month...) are not quantities.
CALENDAR_WORDS = frozenset(
    {"year", "yr", "month", "week", "day", "quarter", "anio", "ano", "mes", "semana", "dia"}
)

MAX_DIMENSION_CARDINALITY = 50


def name_tokens(name: str) -> tuple[str, ...]:
    """Lower-case, accent-free words of a column name (snake, kebab, camel or spaced)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", ascii_name)
    return tuple(t.lower() for t in re.split(r"[^A-Za-z0-9]+", spaced) if t)


def aggregation_for(name: str) -> Aggregation:
    """Sum additive quantities, average intensive ones (prices, rates, scores...).

    A strong money word wins (``total_price`` is summed); otherwise any intensive
    word means average (``aspect_ratio_value``, ``unit_price``).
    """
    words = set(name_tokens(name))
    if words & set(STRONG_SUM):
        return Aggregation.SUM
    if words & AVG_WORDS:
        return Aggregation.AVG
    return Aggregation.SUM


def _measure_rank(col: ColumnProfile) -> tuple[int, int, int, int]:
    words = name_tokens(col.name)
    strong = max(
        (len(STRONG_SUM) - STRONG_SUM.index(w) for w in words if w in STRONG_SUM), default=0
    )
    weak = int(any(w in WEAK_SUM for w in words))
    additive = int(aggregation_for(col.name) is Aggregation.SUM)
    return (strong, weak, additive, col.distinct)


def candidate_measures(profile: DatasetProfile) -> tuple[str, ...]:
    """Numeric, non-identifier columns that vary, best candidates first."""
    cols = [
        c
        for c in profile.columns_of(ColumnKind.NUMERIC)
        if c.distinct > 2
        and not looks_like_identifier(c.name)
        and not CALENDAR_WORDS & set(name_tokens(c.name))
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
    aggregation: Aggregation | None = None,
) -> Roles:
    """Pick roles automatically; explicit arguments win.

    ``aggregation`` is ignored when there is no measure (rows are counted).
    """
    if measure is None:
        measures = candidate_measures(profile)
        measure = measures[0] if measures else None
    if date is None:
        dates = candidate_dates(profile)
        date = dates[0] if dates else None
    if measure is None:
        agg = Aggregation.COUNT
    elif aggregation is not None and aggregation is not Aggregation.COUNT:
        agg = aggregation
    else:
        agg = aggregation_for(measure)
    return Roles(
        measure=measure,
        aggregation=agg,
        date=date,
        dimensions=candidate_dimensions(profile),
    )
