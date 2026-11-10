from __future__ import annotations

import datetime as dt

import duckdb
import polars as pl
import pytest
from streamlit.testing.v1 import AppTest

from insights_forge import breakdowns, charts
from insights_forge.insights import Aggregation, Granularity, candidate_dates, generate_insights
from insights_forge.profiling import DatasetProfile, profile_table
from tests.test_app import APP

# A country-year panel like UN/World Bank data: integer `year`, uneven coverage
# (the last year has only 2 of 30 countries) and two near-identical columns.
PANEL = """
SELECT
    'C' || c AS country,
    ['North', 'South', 'East'][c % 3 + 1] AS region,
    2000 + y AS year,
    (100 + 10 * c + 5 * y) AS homicides,
    (100 + 10 * c + 5 * y) + (c % 2) AS homicides_total_est,
    (1.0 + c % 7) * (1 + y / 10) AS homicide_rate
FROM range(30) a(c), range(11) b(y)
WHERE y < 10 OR c < 2
"""


@pytest.fixture
def panel(con: duckdb.DuckDBPyConnection) -> tuple[pl.DataFrame, DatasetProfile]:
    con.execute(f"CREATE TABLE dataset AS {PANEL}")
    return con.table("dataset").pl(), profile_table(con)


@pytest.fixture
def sales() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "region": ["N", "N", "S", "S", "E", "W", "X", None],
            "amount": [10.0, 20.0, 5.0, None, 3.0, 2.0, 1.0, 4.0],
        }
    )


# --------------------------------------------------------------------------- panel data


def test_integer_year_is_a_date_candidate(panel: tuple[pl.DataFrame, DatasetProfile]) -> None:
    _, profile = panel
    assert candidate_dates(profile) == ("year",)


def test_panel_trend_is_yearly_and_drops_thin_last_year(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"CREATE TABLE dataset AS {PANEL}")
    report = generate_insights(con, profile_table(con), measure="homicides")
    assert report.roles.date == "year"
    assert report.trend is not None
    assert report.trend.granularity is Granularity.YEAR
    assert report.trend.periods[-1] == dt.date(2009, 1, 1)  # 2010 has 2 of 30 countries
    assert set(report.trend.counts) == {30}


def test_coverage_caveat_for_panel_data(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(
        "CREATE TABLE dataset AS SELECT 'country ' || c AS country, 2000 + y AS year, "
        "10.0 + c AS amount FROM range(6) a(y), range(20) b(c) WHERE c < 10 + 2 * y"
    )
    report = generate_insights(con, profile_table(con))
    assert report.trend is not None
    assert report.trend.entity == "country"
    trend = next(i for i in report.insights if i.kind == "trend")
    assert "number of country values per year ranges from 10 to 20" in trend.detail


def test_transactional_data_is_not_a_panel(con: duckdb.DuckDBPyConnection) -> None:
    """Fewer orders in a month is real activity, not missing coverage."""
    con.execute(
        "CREATE TABLE dataset AS SELECT 'ORD-' || range AS order_id, "
        "['a', 'b'][range % 2 + 1] AS channel, "
        "'2025-01-01'::DATE + (range % 212)::INT AS day, 10.0 + range % 7 AS amount "
        "FROM range(2120) WHERE range % 212 < 181 OR range % 212 % 3 = 1"
    )
    report = generate_insights(con, profile_table(con))
    assert report.trend is not None
    assert report.trend.entity is None
    assert report.trend.periods[-1] == dt.date(2025, 7, 1)  # thin July is kept: it is real
    trend = next(i for i in report.insights if i.kind == "trend")
    assert "reflect coverage" not in trend.detail


def test_near_identical_columns_reported_as_duplicates(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(f"CREATE TABLE dataset AS {PANEL}")
    titles = [i.title for i in generate_insights(con, profile_table(con)).insights]
    assert any("homicides and homicides_total_est are nearly the same measure" in t for t in titles)


# --------------------------------------------------------------------------- breakdowns


def test_rank_sum_folds_tail_into_other(sales: pl.DataFrame) -> None:
    out = breakdowns.rank_categories(sales, "region", "amount", Aggregation.SUM, top=3)
    assert out["category"].to_list() == ["N", "S", "(missing)", "Other (3)"]
    assert out["value"].to_list() == [30.0, 5.0, 4.0, 6.0]
    assert out["is_other"].to_list() == [False, False, False, True]


def test_rank_average_pools_the_tail(sales: pl.DataFrame) -> None:
    out = breakdowns.rank_categories(sales, "region", "amount", Aggregation.AVG, top=1)
    assert out["value"][0] == 15.0
    # Other pools 5 + 3 + 2 + 1 + 4 over 5 non-null values, not an average of averages
    assert out["value"][1] == pytest.approx(3.0)


def test_rank_count_rows(sales: pl.DataFrame) -> None:
    out = breakdowns.rank_categories(sales, "region", None, Aggregation.COUNT)
    assert out.filter(pl.col("category") == "S")["value"][0] == 2


def test_a_real_category_named_other_is_not_the_fold() -> None:
    df = pl.DataFrame({"k": ["Other", "A"], "v": [5.0, 1.0]})
    out = breakdowns.rank_categories(df, "k", "v", Aggregation.SUM)
    assert out["is_other"].to_list() == [False, False]


def test_categories_over_time_with_integer_years(
    panel: tuple[pl.DataFrame, DatasetProfile],
) -> None:
    df, profile = panel
    out = breakdowns.categories_over_time(
        df, profile, "region", "year", "homicide_rate", Aggregation.AVG, top=2
    )
    assert out["category"].unique().len() == 2
    assert out["period"].min() == dt.date(2000, 1, 1)
    keep = tuple(dt.date(2000 + y, 1, 1) for y in range(10))
    limited = breakdowns.categories_over_time(
        df, profile, "region", "year", "homicides", Aggregation.SUM, periods=keep
    )
    assert limited["period"].max() == dt.date(2009, 1, 1)


def test_categories_over_time_needs_a_time_axis(
    panel: tuple[pl.DataFrame, DatasetProfile],
) -> None:
    df, profile = panel
    out = breakdowns.categories_over_time(
        df, profile, "region", "homicides", "homicides", Aggregation.SUM
    )
    assert out.is_empty()


def test_spread_orders_groups_by_median() -> None:
    df = pl.DataFrame(
        {"g": ["lo"] * 3 + ["hi"] * 4 + ["rare"], "v": [1.0, 2, 3, 10, 20, 30, 40, 99]}
    )
    data, order = breakdowns.spread_by_category(df, "g", "v", top=2)
    assert order == ["hi", "lo"]  # most frequent two, highest median first
    assert set(data["category"].to_list()) == {"hi", "lo"}


def test_scatter_points_keep_top_groups() -> None:
    df = pl.DataFrame({"x": list(range(10)), "y": list(range(10)), "g": list("aaaabbbcde")})
    out = breakdowns.scatter_points(df, "x", "y", "g", top=2)
    assert out["group"].to_list().count("Other") == 3


def test_missing_by_column(panel: tuple[pl.DataFrame, DatasetProfile]) -> None:
    _, profile = panel
    assert breakdowns.missing_by_column(profile).is_empty()


# --------------------------------------------------------------------------- charts


def test_ranking_chart_mutes_other(sales: pl.DataFrame) -> None:
    data = breakdowns.rank_categories(sales, "region", "amount", Aggregation.SUM, top=2)
    bar = charts.ranking(data, "total amount").data[0]
    assert bar.marker.color[0] == charts.PALETTES["light"].muted_ink  # "Other" drawn first
    assert list(bar.y)[-1] == "N"  # largest on top


def test_lines_cap_and_palette_order(panel: tuple[pl.DataFrame, DatasetProfile]) -> None:
    df, profile = panel
    data = breakdowns.categories_over_time(
        df, profile, "region", "year", "homicides", Aggregation.SUM
    )
    fig = charts.lines(data, "total homicides", "dark")
    assert len(fig.data) == 3
    assert fig.data[0].line.color == charts.PALETTES["dark"].categorical[0]


def test_scatter_has_fit_line_and_gray_other() -> None:
    df = pl.DataFrame({"x": [1.0, 2, 3, 4, 5, 6], "y": [2.0, 4, 6, 8, 10, 12], "g": list("aabcde")})
    fig = charts.scatter(breakdowns.scatter_points(df, "x", "y", "g", top=1), "x", "y")
    assert fig.data[-1].name == "linear fit"
    assert list(fig.data[-1].y) == pytest.approx([2.0, 12.0])
    assert fig.data[1].marker.color == charts.PALETTES["light"].muted_ink


def test_spread_missing_and_coverage_charts(
    sales: pl.DataFrame, panel: tuple[pl.DataFrame, DatasetProfile]
) -> None:
    data, order = breakdowns.spread_by_category(sales, "region", "amount")
    assert len(charts.spread(data, "amount", order).data) == len(order)
    missing = pl.DataFrame({"column": ["a"], "missing_pct": [40.0], "missing": [4]})
    assert charts.missing_values(missing).data[0].text == ("40%",)


# --------------------------------------------------------------------------- app


def test_compare_tab_views() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.button[0].click().run()
    assert at.selectbox(key="cmp_dim").value == "region"
    for view in ("Over time", "Spread", "Ranking"):
        at.segmented_control(key="cmp_view").set_value(view).run()
        assert not at.exception
    at.selectbox(key="cmp_measure").select("(row count)").run()
    assert not at.exception


def test_scatter_explorer() -> None:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.button[0].click().run()
    assert (at.selectbox(key="sc_x").value, at.selectbox(key="sc_y").value) == (
        "quantity",
        "total",
    )
    at.selectbox(key="sc_color").select("region").run()
    assert not at.exception


@pytest.mark.parametrize(
    ("values", "positive", "skewed"),
    [
        ([1.0, 2.0, 3.0, 500.0], True, True),
        ([1.0, 2.0, 3.0], True, False),
        ([-1.0, 2.0, 900.0], False, False),
        ([], False, False),
    ],
)
def test_log_scale_heuristics(values: list[float], positive: bool, skewed: bool) -> None:
    series = pl.Series(values, dtype=pl.Float64)
    assert breakdowns.is_positive(series) is positive
    assert breakdowns.is_heavily_skewed(series) is skewed


def test_spread_log_axis() -> None:
    data = pl.DataFrame({"category": ["a", "a", "b"], "v": [1.0, 1000.0, 5.0]})
    fig = charts.spread(data, "v", ["a", "b"], log=True)
    assert fig.layout.xaxis.type == "log"
    assert fig.layout.xaxis.title.text == "v (log scale)"


def test_wrap_label() -> None:
    assert charts.wrap_label("Middle East, North Africa, Afghanistan & Pakistan") == (
        "Middle East, North<br>Africa, Afghanistan &<br>Pakistan"
    )
    assert charts.wrap_label("West") == "West"
    assert charts.wrap_label("") == ""
