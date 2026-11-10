"""Plotly figure builders shared by the web app and the HTML report.

Pure functions (data in, figure out) so they can be unit-tested without a
running Streamlit server. Colors come from a CVD-validated palette with
separately tuned light and dark steps.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import plotly.express as px
import plotly.graph_objects as go
import polars as pl

from insights_forge.insights import TrendSeries
from insights_forge.profiling import ColumnProfile, DatasetProfile

Mode = Literal["light", "dark"]


@dataclass(frozen=True, slots=True)
class Palette:
    series: str  # single-series marks
    negative: str  # diverging pole (r = -1)
    neutral: str  # diverging midpoint (r = 0)
    positive: str  # diverging pole (r = +1)
    muted_ink: str
    grid: str


PALETTES: dict[Mode, Palette] = {
    "light": Palette("#2a78d6", "#e34948", "#f0efec", "#2a78d6", "#898781", "#e1e0d9"),
    "dark": Palette("#3987e5", "#e66767", "#383835", "#3987e5", "#898781", "#2c2c2a"),
}

MAX_CHART_ROWS = 50_000


def chart_sample(df: pl.DataFrame) -> pl.DataFrame:
    """Down-sample large frames so the browser doesn't receive millions of points."""
    return df.sample(n=MAX_CHART_ROWS, seed=0) if df.height > MAX_CHART_ROWS else df


def _style(fig: go.Figure, palette: Palette, height: int = 360) -> go.Figure:
    fig.update_layout(
        height=height,
        margin={"l": 8, "r": 8, "t": 8, "b": 8},
        showlegend=False,
        hoverlabel={"namelength": 0},
    )
    fig.update_xaxes(gridcolor=palette.grid, zeroline=False)
    fig.update_yaxes(gridcolor=palette.grid, zeroline=False)
    return fig


def distribution(
    df: pl.DataFrame,
    column: str,
    mode: Mode = "light",
    value_range: tuple[float, float] | None = None,
) -> go.Figure:
    """Histogram with a box-plot strip on top (shows median, IQR and outliers).

    ``value_range`` keeps only values inside ``[low, high]``, e.g. the IQR fences.
    """
    palette = PALETTES[mode]
    data = df.select(column).drop_nulls()
    if value_range is not None:
        data = data.filter(pl.col(column).is_between(*value_range))
    fig = px.histogram(
        chart_sample(data),
        x=column,
        marginal="box",
        color_discrete_sequence=[palette.series],
    )
    fig.update_layout(bargap=0.04, yaxis_title="Rows")
    return _style(fig, palette)


def over_time(df: pl.DataFrame, column: str, mode: Mode = "light") -> go.Figure:
    """Row count over time for a date/timestamp column."""
    palette = PALETTES[mode]
    fig = px.histogram(
        chart_sample(df.select(column).drop_nulls()),
        x=column,
        color_discrete_sequence=[palette.series],
    )
    fig.update_layout(bargap=0.04, yaxis_title="Rows")
    return _style(fig, palette)


def top_values(col: ColumnProfile, mode: Mode = "light") -> go.Figure:
    """Horizontal bars of the most frequent values, largest on top."""
    palette = PALETTES[mode]
    values = list(reversed(col.top_values))
    fig = go.Figure(
        go.Bar(
            x=[v.count for v in values],
            y=[v.value for v in values],
            orientation="h",
            marker={"color": palette.series, "cornerradius": 4},
            text=[f"{v.count:,}" for v in values],
            textposition="outside",
            cliponaxis=False,
            hovertemplate="%{y}: %{x:,} rows<extra></extra>",
        )
    )
    fig.update_layout(bargap=0.3, xaxis_title="Rows")
    return _style(fig, palette, height=max(200, 48 * len(values) + 60))


def correlation_heatmap(profile: DatasetProfile, mode: Mode = "light") -> go.Figure:
    """Symmetric Pearson matrix on a diverging scale fixed to [-1, 1]."""
    palette = PALETTES[mode]
    in_matrix = {n for c in profile.correlations for n in (c.column_a, c.column_b)}
    names = [c.name for c in profile.columns if c.name in in_matrix]  # keep table order
    index = {name: i for i, name in enumerate(names)}
    matrix = [[1.0 if i == j else 0.0 for j in range(len(names))] for i in range(len(names))]
    for c in profile.correlations:
        i, j = index[c.column_a], index[c.column_b]
        matrix[i][j] = matrix[j][i] = c.pearson

    fig = px.imshow(
        matrix,
        x=names,
        y=names,
        zmin=-1,
        zmax=1,
        text_auto=".2f",
        aspect="auto",
        color_continuous_scale=[
            (0.0, palette.negative),
            (0.5, palette.neutral),
            (1.0, palette.positive),
        ],
    )
    fig.update_traces(hovertemplate="%{y} vs %{x}<br>r = %{z:.3f}<extra></extra>", xgap=2, ygap=2)
    fig.update_layout(coloraxis_colorbar={"title": "r", "thickness": 12})
    return _style(fig, palette, height=max(320, 56 * len(names) + 80))


def trend(series: TrendSeries, label: str, mode: Mode = "light") -> go.Figure:
    """Measure per period as a 2px line with markers, plus the fitted linear trend."""
    palette = PALETTES[mode]
    x = list(series.periods)
    fig = go.Figure(
        go.Scatter(
            x=x,
            y=list(series.values),
            mode="lines+markers",
            line={"color": palette.series, "width": 2},
            marker={"size": 8, "color": palette.series},
            customdata=list(series.labels),
            hovertemplate=f"%{{customdata}}<br>{label}: %{{y:,.4~g}}<extra></extra>",
        )
    )
    if series.slope_pct is not None and len(x) >= 2:
        n = len(series.values)
        mean = sum(series.values) / n
        slope = series.slope_pct / 100 * mean
        fitted = [mean + slope * (i - (n - 1) / 2) for i in range(n)]
        fig.add_trace(
            go.Scatter(
                x=x,
                y=fitted,
                mode="lines",
                line={"color": palette.muted_ink, "width": 1.5, "dash": "dot"},
                hoverinfo="skip",
            )
        )
    fig.update_layout(yaxis_title=label, hovermode="x")
    return _style(fig, palette)


def transparent(fig: go.Figure) -> go.Figure:
    """Let the host page's background (light or dark) show through."""
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font={"color": PALETTES["light"].muted_ink},
    )
    # A translucent neutral grid reads on both light and dark pages.
    fig.update_xaxes(gridcolor="rgba(137,135,129,0.25)")
    fig.update_yaxes(gridcolor="rgba(137,135,129,0.25)")
    return fig
