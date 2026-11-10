from __future__ import annotations

import datetime as dt

import duckdb
import pytest

from insights_forge.profiling import ColumnKind, ProfileConfig, profile_table
from insights_forge.profiling.profiler import classify, quote_ident
from tests.conftest import MakeProfile


@pytest.mark.parametrize(
    ("dtype", "kind"),
    [
        ("BIGINT", ColumnKind.NUMERIC),
        ("DOUBLE", ColumnKind.NUMERIC),
        ("DECIMAL(10,2)", ColumnKind.NUMERIC),
        ("VARCHAR", ColumnKind.TEXT),
        ("DATE", ColumnKind.TEMPORAL),
        ("TIMESTAMP WITH TIME ZONE", ColumnKind.TEMPORAL),
        ("BOOLEAN", ColumnKind.BOOLEAN),
        ("INTEGER[]", ColumnKind.OTHER),
        ("STRUCT(a INTEGER)", ColumnKind.OTHER),
    ],
)
def test_classify(dtype: str, kind: ColumnKind) -> None:
    assert classify(dtype) is kind


def test_quote_ident_escapes_quotes() -> None:
    assert quote_ident('we"ird') == '"we""ird"'


def test_dataset_level_metrics(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT * FROM (VALUES (1, 'a'), (1, 'a'), (2, NULL), (3, 'c')) t(x, y)")
    assert (p.rows, p.n_columns) == (4, 2)
    assert p.duplicate_rows == 1
    assert p.missing_cells == 1
    assert p.completeness_pct == pytest.approx(87.5)


def test_numeric_stats(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest([0, 1, 2, 3, 4, -5, NULL]) AS v")
    col = p.column("v")
    assert col.kind is ColumnKind.NUMERIC
    assert (col.count, col.nulls, col.distinct) == (6, 1, 6)
    assert col.null_pct == pytest.approx(100 / 7)
    stats = col.numeric
    assert stats is not None
    assert (stats.min, stats.max, stats.median) == (-5, 4, 1.5)
    assert stats.mean == pytest.approx(5 / 6)
    assert (stats.zeros, stats.negatives) == (1, 1)


def test_outliers_use_tukey_fences(make_profile: MakeProfile) -> None:
    # q1=2, q3=4 (IQR=2) -> fences with factor 3 are [-4, 10]
    p = make_profile("SELECT unnest([1, 2, 2, 3, 3, 3, 4, 4, 5, 1000]) AS v")
    stats = p.column("v").numeric
    assert stats is not None
    assert stats.outliers_high == 1
    assert stats.outliers_low == 0
    assert stats.upper_fence < 1000


def test_outliers_skipped_when_iqr_is_zero(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest([0, 0, 0, 0, 0, 0, 0, 0.1]) AS v")
    stats = p.column("v").numeric
    assert stats is not None
    assert stats.iqr == 0
    assert stats.outliers == 0


def test_iqr_factor_is_configurable(con: duckdb.DuckDBPyConnection) -> None:
    # q1=3, q3=7 (IQR=4): upper fence is 13 with factor 1.5 and 19 with factor 3
    con.execute("CREATE TABLE dataset AS SELECT unnest([1, 2, 3, 4, 5, 6, 7, 8, 15]) AS v")
    strict = profile_table(con, config=ProfileConfig(iqr_factor=1.5)).column("v").numeric
    lenient = profile_table(con, config=ProfileConfig(iqr_factor=3.0)).column("v").numeric
    assert strict is not None
    assert lenient is not None
    assert strict.outliers == 1
    assert lenient.outliers == 0


def test_text_stats_and_top_values(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest(['b', 'a', 'b', ' c ', '  ', 'b', NULL]) AS v")
    col = p.column("v")
    assert col.kind is ColumnKind.TEXT
    assert col.text is not None
    assert (col.text.blank, col.text.padded) == (1, 1)
    assert (col.text.min_length, col.text.max_length) == (1, 3)
    assert col.top_values[0].value == "b"
    assert col.top_values[0].count == 3


def test_top_values_limited_to_top_k(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE dataset AS SELECT (range % 10)::VARCHAR AS v FROM range(100)")
    p = profile_table(con, config=ProfileConfig(top_k=3))
    assert len(p.column("v").top_values) == 3


def test_temporal_stats(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest(['2025-01-01'::DATE, '2025-03-02'::DATE, NULL]) AS d")
    col = p.column("d")
    assert col.kind is ColumnKind.TEMPORAL
    assert col.temporal is not None
    assert col.temporal.min == dt.date(2025, 1, 1)
    assert col.temporal.span_days == 60


def test_boolean_column_has_top_values(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest([true, true, false]) AS b")
    col = p.column("b")
    assert col.kind is ColumnKind.BOOLEAN
    assert col.top_values[0].value == "true"


def test_all_null_column_has_no_stats(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT NULL::DOUBLE AS v FROM range(3)")
    col = p.column("v")
    assert (col.count, col.nulls) == (0, 3)
    assert col.numeric is None


def test_correlations_sorted_by_strength(make_profile: MakeProfile) -> None:
    p = make_profile(
        "SELECT range AS x, range * 2 AS y, -range AS z, (range * 7919) % 13 AS noise "
        "FROM range(50)"
    )
    top = p.correlations[0]
    assert abs(top.pearson) == pytest.approx(1.0)
    assert top.strength == "strong"
    assert len(p.correlations) == 6  # C(4, 2)
    assert [abs(c.pearson) for c in p.correlations] == sorted(
        (abs(c.pearson) for c in p.correlations), reverse=True
    )


def test_constant_numeric_columns_excluded_from_correlations(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS x, 1 AS constant FROM range(10)")
    assert p.correlations == ()


def test_column_names_with_special_characters(make_profile: MakeProfile) -> None:
    p = make_profile('SELECT 1 AS "total sales ($)", 2 AS "we""ird"')
    assert [c.name for c in p.columns] == ["total sales ($)", 'we"ird']


def test_empty_table(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT 1 AS x WHERE false")
    assert p.rows == 0
    assert p.completeness_pct == 100.0
    assert p.issues == ()


def test_connection_usable_after_profiling(con: duckdb.DuckDBPyConnection) -> None:
    """Regression: DuckDB 2.0 blocks a connection that has a partially fetched result."""
    con.execute("CREATE TABLE dataset AS SELECT range AS x FROM range(5)")
    relation = con.table("dataset")
    profile_table(con)
    assert relation.pl().height == 5


def test_unknown_column_raises(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT 1 AS x")
    with pytest.raises(KeyError):
        p.column("y")
