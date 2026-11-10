from __future__ import annotations

import polars as pl
import pytest

from insights_forge.app import charts
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


def test_correlation_heatmap_is_symmetric(make_profile: MakeProfile) -> None:
    p = make_profile("SELECT range AS a, range * 2 AS b, (range * 7) % 5 AS c FROM range(20)")
    z = charts.correlation_heatmap(p).data[0].z
    n = len(z)
    assert n == 3
    assert all(z[i][i] == 1.0 for i in range(n))
    assert all(z[i][j] == z[j][i] for i in range(n) for j in range(n))


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
    assert list(charts.correlation_heatmap(p).data[0].x) == ["c", "a", "b"]
