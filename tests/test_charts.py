from __future__ import annotations

import polars as pl
import pytest

from insights_forge import charts
from tests.conftest import MakeProfile


@pytest.fixture
def df() -> pl.DataFrame:
    return pl.DataFrame({"x": [1.0, 2.0, 2.0, 3.0, None], "d": ["2025-01-01"] * 5}).with_columns(
        pl.col("d").str.to_date()
    )


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_distribution_uses_series_color(df: pl.DataFrame, mode: charts.Mode) -> None:
    fig = charts.distribution(df, "x", mode)
    assert fig.data[0].marker.color == charts.PALETTES[mode].series


def test_over_time(df: pl.DataFrame) -> None:
    assert len(charts.over_time(df, "d").data) == 1


def test_top_values_largest_on_top(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT unnest(['a', 'b', 'b', 'c', 'c', 'c']) AS v")
    bar = charts.top_values(p.column("v")).data[0]
    assert list(bar.y) == ["a", "b", "c"]  # plotly draws the last bar on top


def test_correlation_heatmap_lower_triangle(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS a, range * 2 AS b, (range * 7) % 5 AS c FROM range(20)")
    heat = charts.correlation_heatmap(p).data[0]
    assert list(heat.y) == ["b", "c"]
    assert list(heat.x) == ["a", "b"]
    z = heat.z
    assert z[0][0] == pytest.approx(1.0)  # b vs a
    assert z[0][1] is None or z[0][1] != z[0][1]  # above the diagonal: empty
    assert z[1][0] == pytest.approx(
        next(c.pearson for c in p.correlations if {c.column_a, c.column_b} == {"a", "c"})
    )


def test_chart_sample_caps_rows() -> None:
    big = pl.DataFrame({"x": range(charts.MAX_CHART_ROWS + 10)})
    assert charts.chart_sample(big).height == charts.MAX_CHART_ROWS
    small = pl.DataFrame({"x": [1, 2]})
    assert charts.chart_sample(small) is small


def test_distribution_value_range_filters_values(df: pl.DataFrame) -> None:
    fig = charts.distribution(df, "x", value_range=(1.5, 2.5))
    assert sorted(fig.data[0].x) == [2.0, 2.0]


def test_heatmap_keeps_table_column_order(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT (range * 7) % 5 AS c, range AS a, range * 2 AS b FROM range(20)")
    heat = charts.correlation_heatmap(p).data[0]
    assert list(heat.y) == ["a", "b"]
    assert list(heat.x) == ["c", "a"]
