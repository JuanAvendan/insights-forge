"""Rule-based insight generation.

Each generator answers one analyst question with SQL pushed down to DuckDB and
returns plain-language :class:`Insight` objects with an importance score. The
engine keeps the best few per kind, then ranks everything by score.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import duckdb

from insights_forge.formatting import fmt_number, sentence_case
from insights_forge.insights.models import (
    Aggregation,
    Granularity,
    Insight,
    InsightKind,
    InsightReport,
    Roles,
    TrendSeries,
)
from insights_forge.insights.roles import detect_roles
from insights_forge.profiling import DatasetProfile
from insights_forge.profiling.profiler import quote_ident

MIN_TREND_PERIODS = 3
MIN_SEGMENT_ROWS = 10
MAX_PER_KIND = 3
SINGLE_ROW_DOMINANCE = 0.25
MISSING_CAVEAT_PCT = 20.0


def change_text(first: float, last: float) -> str:
    """'+6%' for normal changes, '1,437x' when the value grew more than tenfold."""
    ratio = last / first
    return f"{ratio:,.0f}x" if ratio >= 10 else f"{ratio - 1:+.0%}"


def total_of(measure: str) -> str:
    """'total sales' but not 'total total'."""
    return measure if "total" in measure.lower() else f"total {measure}"


@dataclass(frozen=True, slots=True)
class _Context:
    con: duckdb.DuckDBPyConnection
    table: str  # quoted
    profile: DatasetProfile
    roles: Roles

    @property
    def agg_sql(self) -> str:
        if self.roles.measure is None:
            return "count(*)"
        return f"{self.roles.aggregation}({quote_ident(self.roles.measure)})"

    def rows(self, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
        return self.con.execute(sql, params).fetchall()


# --------------------------------------------------------------------------- trend


def granularity_for(span_days: int) -> Granularity:
    if span_days <= 62:
        return Granularity.DAY
    if span_days <= 182:
        return Granularity.WEEK
    if span_days <= 3 * 366:
        return Granularity.MONTH
    if span_days <= 10 * 366:
        return Granularity.QUARTER
    return Granularity.YEAR  # keeps long histories at a readable ~10-150 points


def period_end(start: dt.date, g: Granularity) -> dt.date:
    match g:
        case Granularity.DAY:
            return start
        case Granularity.WEEK:
            return start + dt.timedelta(days=6)
        case Granularity.YEAR:
            return dt.date(start.year, 12, 31)
        case Granularity.MONTH | Granularity.QUARTER:
            months = 1 if g is Granularity.MONTH else 3
            year, month = divmod(start.month - 1 + months, 12)
            return dt.date(start.year + year, month + 1, 1) - dt.timedelta(days=1)


def linear_fit(values: list[float]) -> tuple[float, float]:
    """Least-squares slope and R² of ``values`` against 0..n-1."""
    n = len(values)
    xs = range(n)
    x_mean, y_mean = (n - 1) / 2, sum(values) / n
    sxx = sum((x - x_mean) ** 2 for x in xs)
    sxy = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, values, strict=True))
    syy = sum((y - y_mean) ** 2 for y in values)
    slope = sxy / sxx
    r2 = (sxy * sxy) / (sxx * syy) if syy else 0.0
    return slope, r2


def build_trend(ctx: _Context) -> TrendSeries | None:
    date = ctx.roles.date
    if date is None:
        return None
    stats = ctx.profile.column(date).temporal
    if stats is None:
        return None

    g = granularity_for(stats.span_days)
    d = quote_ident(date)
    rows = ctx.rows(
        f"SELECT date_trunc('{g}', {d})::DATE AS p, {ctx.agg_sql} AS v "
        f"FROM {ctx.table} WHERE {d} IS NOT NULL GROUP BY p ORDER BY p"
    )
    points = [(p, float(v)) for p, v in rows if v is not None]
    # A trailing period that the data does not fully cover would look like a drop.
    if len(points) > MIN_TREND_PERIODS and stats.max < period_end(points[-1][0], g):
        points.pop()
    if len(points) < MIN_TREND_PERIODS:
        return None

    values = [v for _, v in points]
    mean = sum(values) / len(values)
    slope, r2 = linear_fit(values)
    return TrendSeries(
        granularity=g,
        periods=tuple(p for p, _ in points),
        values=tuple(values),
        slope_pct=100 * slope / mean if mean else None,
        r2=r2,
    )


def trend_insights(ctx: _Context, trend: TrendSeries | None) -> Iterable[Insight]:
    if trend is None or trend.slope_pct is None or trend.r2 is None:
        return
    label, g = ctx.roles.measure_label, trend.granularity
    first, last = trend.values[0], trend.values[-1]
    labels = trend.labels
    change = f" ({change_text(first, last)})" if first > 0 else ""
    span = f"From {labels[0]} ({fmt_number(first)}) to {labels[-1]} ({fmt_number(last)}){change}."
    cols = tuple(c for c in (ctx.roles.measure, ctx.roles.date) if c)
    caveat = _missing_caveat(ctx)

    if trend.r2 >= 0.5 and abs(trend.slope_pct) >= 1:
        direction = "growing" if trend.slope_pct > 0 else "declining"
        yield Insight(
            InsightKind.TREND,
            f"{sentence_case(label)} is {direction} about {abs(trend.slope_pct):.1f}% per {g}",
            f"{span} A linear trend explains {trend.r2:.0%} of the variation.{caveat}",
            score=0.55 + 0.4 * trend.r2,
            columns=cols,
        )
    elif trend.r2 < 0.2:
        yield Insight(
            InsightKind.TREND,
            f"No clear trend in {label} over time",
            f"{span} Values fluctuate without a consistent direction "
            f"(linear fit R² = {trend.r2:.2f}).{caveat}",
            score=0.3,
            columns=cols,
        )
    else:
        direction = "upward" if trend.slope_pct > 0 else "downward"
        yield Insight(
            InsightKind.TREND,
            f"{sentence_case(label)} shows a weak {direction} trend",
            f"{span} About {trend.slope_pct:+.1f}% per {g}, but the fit is loose "
            f"(R² = {trend.r2:.2f}).{caveat}",
            score=0.4,
            columns=cols,
        )

    mean = sum(trend.values) / len(trend.values)
    if mean <= 0 or len(trend.values) < 4:
        return
    hi = max(range(len(trend.values)), key=trend.values.__getitem__)
    lo = min(range(len(trend.values)), key=trend.values.__getitem__)
    spread = (trend.values[hi] - trend.values[lo]) / mean
    detail = (
        f"{fmt_number(trend.values[hi])} ({trend.values[hi] / mean - 1:+.0%} vs. the average {g}). "
        f"The weakest was {labels[lo]} at {fmt_number(trend.values[lo])} "
        f"({trend.values[lo] / mean - 1:+.0%})."
    )
    single = _largest_row_share(ctx, trend.periods[hi], g)
    if single is not None and single >= SINGLE_ROW_DOMINANCE:
        detail += (
            f" A single row contributes {single:.0%} of that {g}: check it is not an outlier "
            "before reading it as a real peak."
        )
    yield Insight(
        InsightKind.PEAK,
        f"{labels[hi]} was the strongest {g} for {label}",
        detail,
        score=min(0.8, 0.35 + spread / 4),
        columns=cols,
    )


def _missing_caveat(ctx: _Context) -> str:
    """Warn when a summed measure is mostly missing: totals then track data coverage."""
    measure = ctx.roles.measure
    if measure is None:
        return ""
    pct = ctx.profile.column(measure).null_pct
    if pct < MISSING_CAVEAT_PCT:
        return ""
    note = f" Note: {measure} is missing in {pct:.0f}% of rows"
    if ctx.roles.aggregation is Aggregation.SUM:
        return note + ", so totals also reflect how many rows have a value; consider the average."
    return note + "."


def _largest_row_share(ctx: _Context, period: dt.date, g: Granularity) -> float | None:
    """Share of a period's total that comes from its single largest row (sum measures)."""
    measure, date = ctx.roles.measure, ctx.roles.date
    if measure is None or date is None or ctx.roles.aggregation is not Aggregation.SUM:
        return None
    m, d = quote_ident(measure), quote_ident(date)
    [(largest, total)] = ctx.rows(
        f"SELECT max({m}), sum({m}) FROM {ctx.table} WHERE date_trunc('{g}', {d})::DATE = ?",
        [period],
    )
    if largest is None or not total or total <= 0:
        return None
    return float(largest / total)


# --------------------------------------------------------------------------- segments


def concentration_insights(ctx: _Context) -> Iterable[Insight]:
    """Is one category responsible for a disproportionate share?"""
    additive = ctx.roles.measure is not None and ctx.roles.aggregation is Aggregation.SUM
    measure = ctx.roles.measure
    additive = additive and measure is not None
    share_sql = f"sum({quote_ident(measure)})" if additive and measure else "count(*)"
    share_label = total_of(measure) if additive and measure else "rows"

    for dim in ctx.roles.dimensions:
        d = quote_ident(dim)
        rows = ctx.rows(
            f"SELECT CAST({d} AS VARCHAR) AS k, {share_sql} AS v FROM {ctx.table} "
            f"WHERE {d} IS NOT NULL GROUP BY k ORDER BY v DESC"
        )
        values = [float(v) for _, v in rows if v is not None]
        total = sum(values)
        if len(values) < 2 or total <= 0 or min(values) < 0:
            continue
        top_key, top_share = rows[0][0], values[0] / total
        k = len(values)
        lift = top_share * k
        if lift < 1.3:
            continue
        detail = f"Across {k} {dim} values; an even split would be {1 / k:.0%} each."
        if k >= 5:
            running, n80 = 0.0, 0
            for v in values:
                running += v
                n80 += 1
                if running / total >= 0.8:
                    break
            detail += f" The top {n80} of {k} make up 80%."
        yield Insight(
            InsightKind.CONCENTRATION,
            f"{top_key} accounts for {top_share:.0%} of {share_label}",
            detail,
            score=min(0.85, 0.3 + (lift - 1) * 0.25),
            columns=(dim,),
        )


def segment_insights(ctx: _Context) -> Iterable[Insight]:
    """Does the average measure differ a lot between groups?"""
    measure = ctx.roles.measure
    if measure is None:
        return
    m = quote_ident(measure)
    for dim in ctx.roles.dimensions:
        d = quote_ident(dim)
        rows = ctx.rows(
            f"SELECT CAST({d} AS VARCHAR) AS k, avg({m}) AS a FROM {ctx.table} "
            f"WHERE {d} IS NOT NULL GROUP BY k HAVING count({m}) >= ? ORDER BY a DESC",
            [MIN_SEGMENT_ROWS],
        )
        if len(rows) < 2:
            continue
        (hi_key, hi), (lo_key, lo) = rows[0], rows[-1]
        if lo is None or hi is None or lo <= 0 or hi / lo < 1.5:
            continue
        ratio = hi / lo
        yield Insight(
            InsightKind.SEGMENT,
            f"Average {measure} is {ratio:.1f}x higher for {hi_key} than for {lo_key}",
            f"By {dim}: {hi_key} averages {fmt_number(hi)} vs. {fmt_number(lo)} for "
            f"{lo_key} (groups with at least {MIN_SEGMENT_ROWS} rows).",
            score=min(0.8, 0.3 + math.log(ratio) / 5),
            columns=(dim, measure),
        )


# --------------------------------------------------------------------------- columns


def correlation_insights(ctx: _Context) -> Iterable[Insight]:
    for c in ctx.profile.correlations:
        if abs(c.pearson) < 0.5:
            continue
        movement = "rise together" if c.pearson > 0 else "move in opposite directions"
        yield Insight(
            InsightKind.CORRELATION,
            f"{c.column_a} and {c.column_b} {movement} (r = {c.pearson:.2f})",
            f"A {c.strength} linear relationship. Correlation alone does not imply causation.",
            score=0.25 + 0.4 * abs(c.pearson),
            columns=(c.column_a, c.column_b),
        )


def distribution_insights(ctx: _Context) -> Iterable[Insight]:
    from insights_forge.insights.roles import candidate_measures

    for name in candidate_measures(ctx.profile):
        stats = ctx.profile.column(name).numeric
        if stats is None or stats.median <= 0 or stats.mean / stats.median < 1.5:
            continue
        yield Insight(
            InsightKind.DISTRIBUTION,
            f"{name} is right-skewed",
            f"Mean {fmt_number(stats.mean)} vs. median {fmt_number(stats.median)}: a few large "
            "values pull the average up, so the median better represents a typical value.",
            score=0.35,
            columns=(name,),
        )


def duplicate_impact_insights(ctx: _Context) -> Iterable[Insight]:
    measure = ctx.roles.measure
    n = ctx.profile.duplicate_rows
    if not n or measure is None or ctx.roles.aggregation is not Aggregation.SUM:
        return
    m = quote_ident(measure)
    [(with_dups, without_dups)] = ctx.rows(
        f"SELECT (SELECT sum({m}) FROM {ctx.table}), "
        f"(SELECT sum({m}) FROM (SELECT DISTINCT * FROM {ctx.table}))"
    )
    if not without_dups or with_dups == without_dups:
        return
    diff = with_dups - without_dups
    yield Insight(
        InsightKind.QUALITY,
        f"Duplicate rows inflate {total_of(measure)} by {diff / without_dups:.1%}",
        f"{n:,} duplicated rows add {fmt_number(diff)} to the total. Deduplicate before "
        "reporting figures.",
        score=0.75,
        columns=(measure,),
    )


Generator = Callable[[_Context], Iterable[Insight]]
GENERATORS: tuple[Generator, ...] = (
    duplicate_impact_insights,
    concentration_insights,
    segment_insights,
    correlation_insights,
    distribution_insights,
)


def generate_insights(
    con: duckdb.DuckDBPyConnection,
    profile: DatasetProfile,
    *,
    measure: str | None = None,
    date: str | None = None,
    aggregation: Aggregation | None = None,
    max_insights: int = 12,
) -> InsightReport:
    """Generate ranked insights for ``profile.table``.

    ``measure``, ``date`` and ``aggregation`` override automatic role detection.
    """
    roles = detect_roles(profile, measure=measure, date=date, aggregation=aggregation)
    ctx = _Context(con=con, table=quote_ident(profile.table), profile=profile, roles=roles)

    trend = build_trend(ctx)
    found = list(trend_insights(ctx, trend))
    for generator in GENERATORS:
        best = sorted(generator(ctx), key=lambda i: i.score, reverse=True)
        found.extend(best[:MAX_PER_KIND])

    # Equivalent columns (e.g. `color` and `color_type`) can yield identical findings.
    unique = {i.title: i for i in sorted(found, key=lambda i: i.score)}.values()
    ranked = sorted(unique, key=lambda i: i.score, reverse=True)[:max_insights]
    return InsightReport(roles=roles, insights=tuple(ranked), trend=trend)
