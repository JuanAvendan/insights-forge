"""Regressions found by running the tool on a real 26k-row movies dataset (1896-2027)."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import duckdb
import pytest

from insights_forge import charts
from insights_forge.cli import main
from insights_forge.formatting import fmt_compact, sentence_case
from insights_forge.insights import (
    Aggregation,
    Granularity,
    TrendSeries,
    candidate_measures,
    generate_insights,
)
from insights_forge.insights.engine import change_text, granularity_for, period_end
from insights_forge.insights.roles import aggregation_for, name_tokens
from insights_forge.profiling import profile_table

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"

# A century of yearly films: budgets grow, most budgets are missing, and two
# categorical columns carry the same information (like `color` / `color_type`).
MOVIES = """
SELECT
    ('1920-01-01'::DATE + INTERVAL (y) YEAR + INTERVAL (d * 30) DAY)::DATE AS release_date,
    1920 + y AS release_year,
    CASE WHEN d % 5 = 0 THEN 1e6 * pow(1.05, y) END AS budget_usd,
    CASE WHEN d % 5 = 0 THEN 3e6 * pow(1.05, y) END AS box_office_usd,
    [1.33, 1.85, 2.39][d % 3 + 1] AS aspect_ratio_value,
    CASE WHEN y < 40 THEN 'black-and-white' ELSE 'color' END AS color,
    CASE WHEN y < 40 THEN 'black-and-white' ELSE 'color' END AS color_type
FROM range(100) r(y), range(12) s(d)
"""


@pytest.fixture
def movies(con: duckdb.DuckDBPyConnection) -> duckdb.DuckDBPyConnection:
    con.execute(f"CREATE TABLE dataset AS {MOVIES}")
    return con


@pytest.mark.parametrize(
    ("name", "tokens"),
    [
        ("box_office_usd", ("box", "office", "usd")),
        ("unitPrice", ("unit", "price")),
        ("Precio Unitario", ("precio", "unitario")),
        ("duración-min", ("duracion", "min")),
    ],
)
def test_name_tokens(name: str, tokens: tuple[str, ...]) -> None:
    assert name_tokens(name) == tokens


@pytest.mark.parametrize(
    ("name", "agg"),
    [
        ("aspect_ratio_value", Aggregation.AVG),  # "value" must not override "ratio"
        ("box_office_usd", Aggregation.SUM),
        ("runtime_min", Aggregation.AVG),
        ("rotten_tomatoes_pct", Aggregation.AVG),
        ("usage_hours", Aggregation.SUM),  # "age" inside a word is not "age"
    ],
)
def test_aggregation_for_real_names(name: str, agg: Aggregation) -> None:
    assert aggregation_for(name) is agg


def test_measure_ranking_prefers_money_and_skips_years(movies: duckdb.DuckDBPyConnection) -> None:
    measures = candidate_measures(profile_table(movies))
    assert measures[:2] == ("box_office_usd", "budget_usd")
    assert "release_year" not in measures
    assert measures.index("aspect_ratio_value") > measures.index("budget_usd")


def test_long_history_is_yearly(movies: duckdb.DuckDBPyConnection) -> None:
    report = generate_insights(movies, profile_table(movies))
    assert report.trend is not None
    assert report.trend.granularity is Granularity.YEAR
    # 1920-2019, minus 2019: its data stops in late November, an incomplete year
    assert len(report.trend.values) == 99


def test_identical_insights_are_deduplicated(movies: duckdb.DuckDBPyConnection) -> None:
    titles = [i.title for i in generate_insights(movies, profile_table(movies)).insights]
    assert len(titles) == len(set(titles))


def test_mostly_missing_measure_gets_a_caveat(movies: duckdb.DuckDBPyConnection) -> None:
    report = generate_insights(movies, profile_table(movies), measure="budget_usd")
    trend = next(i for i in report.insights if i.kind == "trend")
    assert "missing in 75% of rows" in trend.detail  # 3 of 12 films per year have one
    assert "consider the average" in trend.detail


def test_aggregation_override(movies: duckdb.DuckDBPyConnection) -> None:
    profile = profile_table(movies)
    avg = generate_insights(movies, profile, measure="budget_usd", aggregation=Aggregation.AVG)
    assert avg.roles.aggregation is Aggregation.AVG
    assert avg.roles.measure_label == "average budget_usd"
    assert avg.trend is not None
    assert avg.trend.r2 is not None
    assert avg.trend.r2 > 0.7  # per-film budgets grow steadily (exponential, linear fit)
    # COUNT is not a valid override for a measure: fall back to the column's meaning
    auto = generate_insights(movies, profile, measure="budget_usd", aggregation=Aggregation.COUNT)
    assert auto.roles.aggregation is Aggregation.SUM


def test_cli_aggregation_flag(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", str(SAMPLE), "--measure", "total", "--aggregation", "avg"]) == 0
    assert "Analyzing average total" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("span", "g"), [(5 * 365, Granularity.QUARTER), (30 * 365, Granularity.YEAR)]
)
def test_granularity_long_spans(span: int, g: Granularity) -> None:
    assert granularity_for(span) is g


def test_year_period() -> None:
    assert period_end(dt.date(2025, 1, 1), Granularity.YEAR) == dt.date(2025, 12, 31)
    assert Granularity.YEAR.label(dt.date(1971, 1, 1)) == "1971"


@pytest.mark.parametrize(
    ("first", "last", "text"),
    [(100, 106, "+6%"), (100, 50, "-50%"), (50_000, 71_831_908, "1,437x")],
)
def test_change_text(first: float, last: float, text: str) -> None:
    assert change_text(first, last) == text


@pytest.mark.parametrize(
    ("value", "text"),
    [(7_100_000, "7.1M"), (2_350_000_000, "2.35B"), (950, "950"), (-1500, "-1.5K"), (None, "-")],
)
def test_fmt_compact(value: float | None, text: str) -> None:
    assert fmt_compact(value) == text


def test_sentence_case_keeps_the_rest() -> None:
    assert sentence_case("budget_usd by year") == "Budget_usd by year"
    assert sentence_case("averageUSD") == "AverageUSD"
    assert sentence_case("") == ""


def test_trend_chart_hover_is_compact_and_trend_line_not_negative() -> None:
    series = TrendSeries(
        granularity=Granularity.YEAR,
        periods=tuple(dt.date(1920 + i, 1, 1) for i in range(5)),
        values=(0.0, 0.0, 0.0, 1e6, 7.1e6),
        slope_pct=150.0,
        r2=0.6,
    )
    fig = charts.trend(series, "budget_usd")
    assert list(fig.data[0].customdata[-1]) == ["1924", "7.1M"]
    assert min(fig.data[1].y) >= 0
