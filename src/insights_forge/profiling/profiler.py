"""Compute a :class:`DatasetProfile` for a DuckDB table.

All heavy lifting is pushed down to DuckDB as aggregate SQL, so profiling works
on tables much larger than what fits comfortably in a pandas/Polars DataFrame.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import combinations
from typing import Any

import duckdb

from insights_forge.profiling.config import ProfileConfig
from insights_forge.profiling.models import (
    ColumnKind,
    ColumnProfile,
    Correlation,
    DatasetProfile,
    NumericStats,
    TemporalStats,
    TextStats,
    ValueCount,
)
from insights_forge.profiling.quality import run_checks

_NUMERIC_TYPES = frozenset(
    {
        "TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT",
        "UTINYINT", "USMALLINT", "UINTEGER", "UBIGINT", "UHUGEINT",
        "FLOAT", "REAL", "DOUBLE",
    }
)  # fmt: skip


def quote_ident(name: str) -> str:
    """Quote a SQL identifier so any column name is safe to interpolate."""
    return '"' + name.replace('"', '""') + '"'


def classify(dtype: str) -> ColumnKind:
    """Map a DuckDB type name to a :class:`ColumnKind`."""
    t = dtype.upper()
    if t in _NUMERIC_TYPES or t.startswith("DECIMAL"):
        return ColumnKind.NUMERIC
    if t == "BOOLEAN":
        return ColumnKind.BOOLEAN
    if t == "DATE" or t.startswith("TIMESTAMP"):
        return ColumnKind.TEMPORAL
    if t.startswith("VARCHAR"):
        return ColumnKind.TEXT
    return ColumnKind.OTHER


def profile_table(
    con: duckdb.DuckDBPyConnection,
    table: str = "dataset",
    config: ProfileConfig | None = None,
) -> DatasetProfile:
    """Profile every column of ``table`` and run all data-quality checks."""
    cfg = config or ProfileConfig()
    t = quote_ident(table)
    schema = con.sql(f"FROM {t} LIMIT 0")
    rows = int(_one(con, f"SELECT count(*) FROM {t}")[0])

    columns = tuple(
        _profile_column(con, t, name, str(dtype), rows, cfg)
        for name, dtype in zip(schema.columns, schema.types, strict=True)
    )
    distinct_rows = int(_one(con, f"SELECT count(*) FROM (SELECT DISTINCT * FROM {t})")[0])

    profile = DatasetProfile(
        table=table,
        rows=rows,
        columns=columns,
        duplicate_rows=rows - distinct_rows,
        correlations=_correlations(con, t, columns, cfg),
    )
    return replace(profile, issues=run_checks(profile, cfg))


# --------------------------------------------------------------------------- helpers


def _one(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[Any] | None = None
) -> tuple[Any, ...]:
    # fetchall() (not fetchone()) fully consumes the result: since DuckDB 2.0 a
    # partially-read result blocks any further work on the connection.
    rows = con.execute(sql, params).fetchall()
    if not rows:  # pragma: no cover - aggregates always return one row
        msg = f"Query returned no rows: {sql}"
        raise RuntimeError(msg)
    return tuple(rows[0])


def _profile_column(
    con: duckdb.DuckDBPyConnection,
    t: str,
    name: str,
    dtype: str,
    rows: int,
    cfg: ProfileConfig,
) -> ColumnProfile:
    c = quote_ident(name)
    count, distinct = _one(con, f"SELECT count({c}), count(DISTINCT {c}) FROM {t}")
    kind = classify(dtype)
    has_values = count > 0

    return ColumnProfile(
        name=name,
        dtype=dtype,
        kind=kind,
        count=count,
        nulls=rows - count,
        distinct=distinct,
        numeric=_numeric_stats(con, t, c, cfg)
        if has_values and kind is ColumnKind.NUMERIC
        else None,
        text=_text_stats(con, t, c) if has_values and kind is ColumnKind.TEXT else None,
        temporal=_temporal_stats(con, t, c) if has_values and kind is ColumnKind.TEMPORAL else None,
        top_values=_top_values(con, t, c, cfg.top_k)
        if has_values and kind in {ColumnKind.TEXT, ColumnKind.BOOLEAN}
        else (),
    )


def _numeric_stats(
    con: duckdb.DuckDBPyConnection, t: str, c: str, cfg: ProfileConfig
) -> NumericStats:
    mn, mx, mean, std, q1, median, q3, zeros, negatives = _one(
        con,
        f"""
        SELECT min({c})::DOUBLE, max({c})::DOUBLE, avg({c})::DOUBLE, stddev_samp({c})::DOUBLE,
               quantile_cont({c}, 0.25)::DOUBLE, quantile_cont({c}, 0.5)::DOUBLE,
               quantile_cont({c}, 0.75)::DOUBLE,
               count(*) FILTER (WHERE {c} = 0), count(*) FILTER (WHERE {c} < 0)
        FROM {t}
        """,
    )
    iqr = q3 - q1
    if iqr > 0:
        lower, upper = q1 - cfg.iqr_factor * iqr, q3 + cfg.iqr_factor * iqr
        low, high = _one(
            con,
            f"SELECT count(*) FILTER (WHERE {c} < ?), count(*) FILTER (WHERE {c} > ?) FROM {t}",
            [lower, upper],
        )
    else:
        # IQR = 0 (most values identical): Tukey fences are degenerate and would flag
        # every non-mode value, so outlier detection is skipped.
        lower, upper, low, high = mn, mx, 0, 0

    return NumericStats(
        min=mn,
        max=mx,
        mean=mean,
        std=std,
        q1=q1,
        median=median,
        q3=q3,
        zeros=zeros,
        negatives=negatives,
        lower_fence=lower,
        upper_fence=upper,
        outliers_low=low,
        outliers_high=high,
    )


def _text_stats(con: duckdb.DuckDBPyConnection, t: str, c: str) -> TextStats:
    row = _one(
        con,
        f"""
        SELECT min(length({c})), max(length({c})), avg(length({c}))::DOUBLE,
               count(*) FILTER (WHERE trim({c}) = ''),
               count(*) FILTER (WHERE trim({c}) <> '' AND {c} <> trim({c})),
               count(*) FILTER (WHERE TRY_CAST(trim({c}) AS DOUBLE) IS NOT NULL),
               count(*) FILTER (WHERE TRY_CAST(trim({c}) AS DATE) IS NOT NULL)
        FROM {t}
        """,
    )
    return TextStats(*row)


def _temporal_stats(con: duckdb.DuckDBPyConnection, t: str, c: str) -> TemporalStats:
    mn, mx, span = _one(
        con,
        f"SELECT min({c})::DATE, max({c})::DATE, date_diff('day', min({c})::DATE, max({c})::DATE)"
        f" FROM {t}",
    )
    return TemporalStats(min=mn, max=mx, span_days=span)


def _top_values(con: duckdb.DuckDBPyConnection, t: str, c: str, k: int) -> tuple[ValueCount, ...]:
    rows = con.execute(
        f"""
        SELECT CAST({c} AS VARCHAR) AS value, count(*) AS n
        FROM {t} WHERE {c} IS NOT NULL
        GROUP BY value ORDER BY n DESC, value
        LIMIT {int(k)}
        """
    ).fetchall()
    return tuple(ValueCount(value=v, count=n) for v, n in rows)


def _correlations(
    con: duckdb.DuckDBPyConnection,
    t: str,
    columns: tuple[ColumnProfile, ...],
    cfg: ProfileConfig,
) -> tuple[Correlation, ...]:
    numeric = [c.name for c in columns if c.kind is ColumnKind.NUMERIC and c.distinct > 1]
    pairs = list(combinations(numeric[: cfg.max_correlation_columns], 2))
    if not pairs:
        return ()

    exprs = ", ".join(f"corr({quote_ident(a)}::DOUBLE, {quote_ident(b)}::DOUBLE)" for a, b in pairs)
    values = _one(con, f"SELECT {exprs} FROM {t}")
    result = [
        Correlation(column_a=a, column_b=b, pearson=float(r))
        for (a, b), r in zip(pairs, values, strict=True)
        if r is not None
    ]
    return tuple(sorted(result, key=lambda x: abs(x.pearson), reverse=True))
