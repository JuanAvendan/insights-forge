from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from insights_forge.connectors import FileConnector
from insights_forge.insights import (
    Aggregation,
    Granularity,
    InsightKind,
    InsightReport,
    detect_roles,
    generate_insights,
)
from insights_forge.insights.engine import granularity_for, linear_fit, period_end, total_of
from insights_forge.insights.roles import aggregation_for, candidate_dimensions
from insights_forge.profiling import profile_table

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


def _insights(con: duckdb.DuckDBPyConnection, sql: str) -> InsightReport:
    con.execute(f"CREATE OR REPLACE TABLE dataset AS {sql}")
    return generate_insights(con, profile_table(con))


def _kinds(report: InsightReport, kind: InsightKind) -> list[str]:
    return [i.title for i in report.insights if i.kind is kind]


# --------------------------------------------------------------------------- roles


@pytest.mark.parametrize(
    ("name", "agg"),
    [
        ("total", Aggregation.SUM),
        ("revenue", Aggregation.SUM),
        ("unit_price", Aggregation.AVG),
        ("precio_unitario", Aggregation.AVG),
        ("discount", Aggregation.AVG),
        ("total_price", Aggregation.SUM),  # a sum hint wins over an avg hint
        ("weight", Aggregation.SUM),
    ],
)
def test_aggregation_for(name: str, agg: Aggregation) -> None:
    assert aggregation_for(name) is agg


def test_detect_roles_prefers_total_and_skips_ids(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE dataset AS SELECT range AS customer_id, range % 7 AS qty, "
        "range * 3.5 AS total, '2025-01-01'::DATE + range::INT AS day, "
        "['a', 'b', 'c'][range % 3 + 1] AS segment, 'id-' || range AS order_code FROM range(60)"
    )
    roles = detect_roles(profile_table(con))
    assert roles.measure == "total"
    assert roles.aggregation is Aggregation.SUM
    assert roles.date == "day"
    assert roles.dimensions == ("segment",)


def test_detect_roles_overrides(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE dataset AS SELECT range AS a, range * 2.5 AS price FROM range(10)")
    roles = detect_roles(profile_table(con), measure="price")
    assert (roles.measure, roles.aggregation) == ("price", Aggregation.AVG)
    assert roles.measure_label == "average price"


def test_no_numeric_columns_counts_rows(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE dataset AS SELECT ['x', 'y'][range % 2 + 1] AS k FROM range(10)")
    roles = detect_roles(profile_table(con))
    assert roles.measure is None
    assert roles.aggregation is Aggregation.COUNT
    assert roles.measure_label == "row count"


def test_high_cardinality_text_is_not_a_dimension(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE dataset AS SELECT 'name ' || range AS name FROM range(100)")
    assert candidate_dimensions(profile_table(con)) == ()


# --------------------------------------------------------------------------- helpers


@pytest.mark.parametrize(
    ("span", "g"),
    [
        (30, Granularity.DAY),
        (120, Granularity.WEEK),
        (364, Granularity.MONTH),
        (2000, Granularity.QUARTER),
    ],
)
def test_granularity_for(span: int, g: Granularity) -> None:
    assert granularity_for(span) is g


@pytest.mark.parametrize(
    ("start", "g", "end"),
    [
        (dt.date(2025, 2, 1), Granularity.MONTH, dt.date(2025, 2, 28)),
        (dt.date(2025, 12, 1), Granularity.MONTH, dt.date(2025, 12, 31)),
        (dt.date(2025, 10, 1), Granularity.QUARTER, dt.date(2025, 12, 31)),
        (dt.date(2025, 3, 3), Granularity.WEEK, dt.date(2025, 3, 9)),
        (dt.date(2025, 3, 3), Granularity.DAY, dt.date(2025, 3, 3)),
    ],
)
def test_period_end(start: dt.date, g: Granularity, end: dt.date) -> None:
    assert period_end(start, g) == end


def test_granularity_labels() -> None:
    assert Granularity.MONTH.label(dt.date(2025, 5, 1)) == "May 2025"
    assert Granularity.QUARTER.label(dt.date(2025, 7, 1)) == "Q3 2025"
    assert Granularity.DAY.label(dt.date(2025, 7, 1)) == "2025-07-01"


def test_linear_fit() -> None:
    slope, r2 = linear_fit([1.0, 3.0, 5.0, 7.0])
    assert slope == pytest.approx(2.0)
    assert r2 == pytest.approx(1.0)
    assert linear_fit([4.0, 4.0, 4.0]) == (0.0, 0.0)


def test_total_of() -> None:
    assert total_of("sales") == "total sales"
    assert total_of("Total") == "Total"


# --------------------------------------------------------------------------- generators

# One row per day of 2025; `m` is the 0-based month and `d` the 0-based day of month.
MONTHLY = (
    "SELECT day, {value} AS sales FROM ("
    "  SELECT d0 AS day, month(d0) - 1 AS m, day(d0) - 1 AS d"
    "  FROM (SELECT '2025-01-01'::DATE + range::INT AS d0 FROM range(365)))"
)


def test_growing_trend(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(con, MONTHLY.format(value="100 * pow(1.1, m)"))
    assert report.trend is not None
    assert report.trend.granularity is Granularity.MONTH
    assert len(report.trend.values) == 12
    [title] = _kinds(report, InsightKind.TREND)
    assert "growing" in title
    assert report.trend.r2 is not None
    assert report.trend.r2 > 0.9


def test_declining_trend(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(con, MONTHLY.format(value="1000 - 50 * m"))
    assert "declining" in _kinds(report, InsightKind.TREND)[0]


def test_flat_noisy_series_has_no_clear_trend(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(con, MONTHLY.format(value="CASE WHEN m % 2 = 0 THEN 100 ELSE 120 END"))
    assert "No clear trend" in _kinds(report, InsightKind.TREND)[0]


def test_partial_last_period_is_dropped(con: duckdb.DuckDBPyConnection) -> None:
    # Jan-Jul complete (212 days) plus 5 days of August
    report = _insights(
        con,
        "SELECT '2025-01-01'::DATE + range::INT AS day, 10.0 + range % 3 AS sales FROM range(217)",
    )
    assert report.trend is not None
    assert report.trend.granularity is Granularity.MONTH
    assert report.trend.periods[-1] == dt.date(2025, 7, 1)


def test_no_trend_without_dates(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(con, "SELECT range * 1.5 AS sales FROM range(30)")
    assert report.trend is None
    assert not _kinds(report, InsightKind.TREND)


def test_peak_flags_single_row_dominance(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        MONTHLY.format(value="CASE WHEN m = 5 AND d = 0 THEN 100000 ELSE 10 + d END"),
    )
    peak = next(i for i in report.insights if i.kind is InsightKind.PEAK)
    assert peak.title.startswith("Jun 2025")
    assert "single row" in peak.detail


def test_concentration(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        "SELECT CASE WHEN range < 80 THEN 'North' "
        "ELSE ['South', 'East', 'West'][range % 3 + 1] END AS region, "
        "1.0 + range % 4 AS amount FROM range(100)",
    )
    [title] = _kinds(report, InsightKind.CONCENTRATION)
    assert title.startswith("North accounts for")


def test_segment_difference(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        "SELECT ['premium', 'basic'][range % 2 + 1] AS plan, "
        "CASE WHEN range % 2 = 0 THEN 300 ELSE 100 END + range % 5 AS total FROM range(100)",
    )
    [title] = _kinds(report, InsightKind.SEGMENT)
    assert "higher for premium than for basic" in title


def test_correlation_and_skew(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        "SELECT range AS units, range * 2 + (range * 7919) % 150 AS revenue, "
        "range * 3 AS revenue_copy, "
        "CASE WHEN range < 95 THEN 1 + range % 3 ELSE 1000 END AS wait_minutes FROM range(100)",
    )
    correlations = _kinds(report, InsightKind.CORRELATION)
    assert any("units and revenue rise together" in t for t in correlations)
    assert any("units and revenue_copy are nearly the same measure" in t for t in correlations)
    assert any("wait_minutes" in t for t in _kinds(report, InsightKind.DISTRIBUTION))


def test_duplicate_impact(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        "SELECT * FROM (SELECT range AS n, (range + 1) * 10.0 AS amount FROM range(4)) "
        "UNION ALL SELECT 0, 10.0",
    )
    [title] = _kinds(report, InsightKind.QUALITY)
    assert title == "Duplicate rows inflate total amount by 10.0%"


def test_insights_ranked_and_limited(con: duckdb.DuckDBPyConnection) -> None:
    FileConnector(SAMPLE).load(con)
    report = generate_insights(con, profile_table(con), max_insights=5)
    scores = [i.score for i in report.insights]
    assert len(scores) == 5
    assert scores == sorted(scores, reverse=True)


def test_sample_dataset_headline_insights(con: duckdb.DuckDBPyConnection) -> None:
    FileConnector(SAMPLE).load(con)
    report = generate_insights(con, profile_table(con))
    assert report.roles.measure == "total"
    assert report.roles.date == "order_date"
    titles = " | ".join(i.title for i in report.insights)
    assert "Duplicate rows inflate total" in titles
    assert "Laptop accounts for" in titles
    peak = next(i for i in report.insights if i.kind is InsightKind.PEAK)
    assert "single row" in peak.detail  # the injected outlier orders


def test_segment_gap_driven_by_outliers(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        "SELECT ['east', 'west'][range % 2 + 1] AS region, "
        "CASE WHEN range = 1 THEN 50000 ELSE 100 + range % 9 END AS total FROM range(100)",
    )
    segment = next(i for i in report.insights if i.kind is InsightKind.SEGMENT)
    assert "higher for west than for east" in segment.title
    assert "medians are close" in segment.detail


def test_segment_gap_confirmed_by_medians(con: duckdb.DuckDBPyConnection) -> None:
    report = _insights(
        con,
        "SELECT ['premium', 'basic'][range % 2 + 1] AS plan, "
        "CASE WHEN range % 2 = 0 THEN 300 ELSE 100 END + range % 5 AS total FROM range(100)",
    )
    segment = next(i for i in report.insights if i.kind is InsightKind.SEGMENT)
    assert "Medians confirm it" in segment.detail
