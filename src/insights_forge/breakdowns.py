"""Data preparation for comparison charts (pure Polars, no plotting).

Each function answers one question and returns a small, chart-ready frame:

- :func:`rank_categories`      which groups weigh the most?
- :func:`categories_over_time` how does each group evolve?
- :func:`spread_by_category`   is a gap between groups consistent or outlier-driven?
- :func:`scatter_points`       is a correlation real or driven by a few points?
- :func:`missing_by_column`    where is data missing?
"""

from __future__ import annotations

import datetime as dt

import polars as pl

from insights_forge.insights import Aggregation, Granularity
from insights_forge.insights.engine import time_axis
from insights_forge.profiling import DatasetProfile

OTHER = "Other"
MISSING = "(missing)"
MAX_SCATTER_POINTS = 5_000
MAX_SPREAD_ROWS = 50_000
SKEW_FOR_LOG = 20  # max/median ratio from which a log scale reads better

_TRUNCATE_EVERY = {
    Granularity.DAY: "1d",
    Granularity.WEEK: "1w",
    Granularity.MONTH: "1mo",
    Granularity.QUARTER: "1q",
    Granularity.YEAR: "1y",
}


def _category(dim: str) -> pl.Expr:
    return pl.col(dim).cast(pl.String).fill_null(MISSING).alias("category")


def _aggregate(measure: str | None, agg: Aggregation) -> list[pl.Expr]:
    """Per-group sum and count of the measure (enough to derive totals and averages)."""
    rows = pl.len().alias("rows")
    if measure is None or agg is Aggregation.COUNT:
        return [rows, pl.len().cast(pl.Float64).alias("total"), pl.len().alias("n")]
    return [
        rows,
        pl.col(measure).cast(pl.Float64).sum().alias("total"),
        pl.col(measure).count().alias("n"),
    ]


def _value(agg: Aggregation) -> pl.Expr:
    if agg is Aggregation.AVG:
        return (pl.col("total") / pl.col("n")).alias("value")
    return pl.col("total").alias("value")


def rank_categories(
    df: pl.DataFrame,
    dim: str,
    measure: str | None,
    agg: Aggregation,
    top: int = 15,
) -> pl.DataFrame:
    """Groups ranked by value; the tail is folded into a single ``Other`` row.

    Columns: ``category``, ``value``, ``rows``, ``is_other``. Averages of the
    folded tail are computed from its pooled sum and count, not averaged averages.
    """
    grouped = (
        df.group_by(_category(dim))
        .agg(_aggregate(measure, agg))
        .filter(pl.col("n") > 0)
        .with_columns(_value(agg))
        .sort("value", descending=True, nulls_last=True)
    )
    head = grouped.head(top).with_columns(is_other=pl.lit(value=False))
    tail = grouped.slice(top)
    if tail.height:
        other = tail.select(
            pl.lit(f"{OTHER} ({tail.height})").alias("category"),
            pl.col("rows").sum(),
            pl.col("total").sum(),
            pl.col("n").sum(),
        ).with_columns(_value(agg), is_other=pl.lit(value=True))
        head = pl.concat([head, other.select(head.columns)])
    return head.select("category", "value", "rows", "is_other")


def period_expr(profile: DatasetProfile, date: str) -> tuple[pl.Expr, Granularity] | None:
    """Polars expression bucketing ``date`` into periods (same rules as the trend)."""
    axis = time_axis(profile, date)
    if axis is None:
        return None
    if profile.column(date).temporal is None:  # integer years
        return pl.date(pl.col(date), 1, 1).alias("period"), axis.granularity
    every = _TRUNCATE_EVERY[axis.granularity]
    return pl.col(date).cast(pl.Date).dt.truncate(every).alias("period"), axis.granularity


def categories_over_time(
    df: pl.DataFrame,
    profile: DatasetProfile,
    dim: str,
    date: str,
    measure: str | None,
    agg: Aggregation,
    top: int = 5,
    periods: tuple[dt.date, ...] | None = None,
) -> pl.DataFrame:
    """Long frame (``period``, ``category``, ``value``) for the ``top`` groups.

    ``periods`` restricts the output, e.g. to the complete periods of the trend.
    """
    bucket = period_expr(profile, date)
    if bucket is None:
        return pl.DataFrame(schema={"period": pl.Date, "category": pl.String, "value": pl.Float64})
    expr, _ = bucket
    leaders = rank_categories(df, dim, measure, agg, top=top).filter(~pl.col("is_other"))
    order = leaders["category"].to_list()
    out = (
        df.filter(pl.col(date).is_not_null())
        .with_columns(expr, _category(dim))
        .filter(pl.col("category").is_in(order))
        .group_by("period", "category")
        .agg(_aggregate(measure, agg))
        .filter(pl.col("n") > 0)
        .with_columns(_value(agg))
        .select("period", "category", "value")
    )
    if periods is not None:
        out = out.filter(pl.col("period").is_in(list(periods)))
    rank = {name: i for i, name in enumerate(order)}
    return (
        out.with_columns(
            pl.col("category").replace_strict(rank, return_dtype=pl.Int32).alias("_rank")
        )
        .sort("_rank", "period")
        .drop("_rank")
    )


def spread_by_category(
    df: pl.DataFrame, dim: str, measure: str, top: int = 12
) -> tuple[pl.DataFrame, list[str]]:
    """Rows of the ``top`` most frequent groups and those groups ordered by median."""
    data = df.select(_category(dim), pl.col(measure).cast(pl.Float64)).drop_nulls(measure)
    frequent = (
        data.group_by("category").agg(pl.len().alias("n")).sort("n", descending=True).head(top)
    )
    data = data.filter(pl.col("category").is_in(frequent["category"].to_list()))
    order = (
        data.group_by("category")
        .agg(pl.col(measure).median().alias("median"))
        .sort("median", descending=True)["category"]
        .to_list()
    )
    if data.height > MAX_SPREAD_ROWS:
        data = data.sample(n=MAX_SPREAD_ROWS, seed=0)
    return data, order


def is_positive(values: pl.Series) -> bool:
    """True when a log scale is possible (every value > 0)."""
    low = values.min()
    return values.len() > 0 and isinstance(low, int | float) and low > 0


def is_heavily_skewed(values: pl.Series, ratio: float = SKEW_FOR_LOG) -> bool:
    """Positive values whose max dwarfs the median: a log scale reads better."""
    if not is_positive(values):
        return False
    high, median = values.max(), values.median()
    return (
        isinstance(high, int | float) and isinstance(median, int | float) and high > ratio * median
    )


def scatter_points(
    df: pl.DataFrame, x: str, y: str, color: str | None = None, top: int = 3
) -> pl.DataFrame:
    """Rows with both values present; optional ``group`` keeps the ``top`` groups.

    Scatter plots stay readable with at most three colored groups, so the rest
    are labelled ``Other``.
    """
    cols = [pl.col(x).cast(pl.Float64), pl.col(y).cast(pl.Float64)]
    if color is not None:
        cols.append(_category(color).alias("group"))
    data = df.select(cols).drop_nulls([x, y])
    if color is not None:
        leaders = (
            data.group_by("group")
            .agg(pl.len().alias("n"))
            .sort("n", descending=True)
            .head(top)["group"]
            .to_list()
        )
        data = data.with_columns(
            pl.when(pl.col("group").is_in(leaders))
            .then(pl.col("group"))
            .otherwise(pl.lit(OTHER))
            .alias("group")
        )
    if data.height > MAX_SCATTER_POINTS:
        data = data.sample(n=MAX_SCATTER_POINTS, seed=0)
    return data


def missing_by_column(profile: DatasetProfile) -> pl.DataFrame:
    """Columns with missing values, most incomplete first."""
    return (
        pl.DataFrame(
            {
                "column": [c.name for c in profile.columns],
                "missing_pct": [c.null_pct for c in profile.columns],
                "missing": [c.nulls for c in profile.columns],
            },
            schema={"column": pl.String, "missing_pct": pl.Float64, "missing": pl.Int64},
        )
        .filter(pl.col("missing") > 0)
        .sort("missing_pct", descending=True)
    )
