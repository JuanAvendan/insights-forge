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

from insights_forge.breakdowns import OTHER
from insights_forge.formatting import fmt_compact
from insights_forge.insights import Granularity, TrendSeries
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
    # Fixed-order categorical hues, validated for color-vision deficiency as
    # adjacent pairs (bars, lines). Never cycled: extra groups fold into "Other".
    categorical: tuple[str, ...] = ()


PALETTES: dict[Mode, Palette] = {
    "light": Palette(
        "#2a78d6", "#e34948", "#f0efec", "#2a78d6", "#898781", "#e1e0d9",
        categorical=("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300",
                     "#6250d6", "#e34948"),
    ),
    "dark": Palette(
        "#3987e5", "#e66767", "#383835", "#3987e5", "#898781", "#2c2c2a",
        categorical=("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300",
                     "#9085e9", "#e66767"),
    ),
}  # fmt: skip
MAX_LINE_SERIES = 5  # more lines than this turn into spaghetti
MAX_SCATTER_GROUPS = 3  # all-pairs CVD-safe limit for scatter colors

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
    """Lower triangle of the Pearson matrix on a diverging scale fixed to [-1, 1].

    The matrix is symmetric and its diagonal is always 1, so only the cells below
    the diagonal carry information: showing them alone halves the clutter.
    """
    palette = PALETTES[mode]
    in_matrix = {n for c in profile.correlations for n in (c.column_a, c.column_b)}
    names = [c.name for c in profile.columns if c.name in in_matrix]  # keep table order
    index = {name: i for i, name in enumerate(names)}
    full = [[0.0] * len(names) for _ in names]
    for c in profile.correlations:
        i, j = index[c.column_a], index[c.column_b]
        full[i][j] = full[j][i] = c.pearson
    # rows 1..n-1, columns 0..n-2, keep only j < i
    matrix: list[list[float | None]] = [
        [full[i][j] if j < i else None for j in range(len(names) - 1)] for i in range(1, len(names))
    ]

    fig = px.imshow(
        matrix,
        x=names[:-1],
        y=names[1:],
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
    fig.update_xaxes(showgrid=False, tickangle=-35)
    fig.update_yaxes(showgrid=False)
    return _style(fig, palette, height=max(320, 44 * len(names) + 120))


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
            customdata=[
                [p, fmt_compact(v)] for p, v in zip(series.labels, series.values, strict=True)
            ],
            hovertemplate=f"%{{customdata[0]}}<br>{label}: %{{customdata[1]}}<extra></extra>",
        )
    )
    if series.slope_pct is not None and len(x) >= 2:
        n = len(series.values)
        mean = sum(series.values) / n
        slope = series.slope_pct / 100 * mean
        fitted = [mean + slope * (i - (n - 1) / 2) for i in range(n)]
        if min(series.values) >= 0:  # non-negative measure: never draw the trend below zero
            fitted = [max(0.0, y) for y in fitted]
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


# --------------------------------------------------------------------------- comparisons


def ranking(data: pl.DataFrame, label: str, mode: Mode = "light") -> go.Figure:
    """Horizontal bars, largest on top; the folded "Other" bar is muted gray."""
    palette = PALETTES[mode]
    rows = list(reversed(data.rows(named=True)))
    fig = go.Figure(
        go.Bar(
            x=[r["value"] for r in rows],
            y=[wrap_label(r["category"]) for r in rows],
            orientation="h",
            marker={
                "color": [palette.muted_ink if r["is_other"] else palette.series for r in rows],
                "cornerradius": 4,
            },
            text=[fmt_compact(r["value"]) for r in rows],
            textposition="outside",
            cliponaxis=False,
            customdata=[[fmt_compact(r["value"]), f"{r['rows']:,}"] for r in rows],
            hovertemplate=f"%{{y}}<br>{label}: %{{customdata[0]}}<br>"
            "rows: %{customdata[1]}<extra></extra>",
        )
    )
    fig.update_layout(bargap=0.3, xaxis_title=label)
    fig.update_yaxes(automargin=True)
    fig = _style(fig, palette, height=max(220, 40 * len(rows) + 80))
    fig.update_layout(margin={"r": 56})  # room for the value label of the longest bar
    return fig


def wrap_label(text: str, width: int = 22) -> str:
    """Break long category names over lines so bars keep their room on narrow screens."""
    lines: list[str] = []
    for word in text.split():
        if lines and len(lines[-1]) + 1 + len(word) <= width:
            lines[-1] += f" {word}"
        else:
            lines.append(word)
    return "<br>".join(lines) or text


def lines(
    data: pl.DataFrame,
    label: str,
    mode: Mode = "light",
    granularity: Granularity | None = None,
) -> go.Figure:
    """One 2px line per group (rows already ordered by rank), with a legend.

    ``granularity`` formats periods in tooltips ("2005", "Q3 2005", "May 2005").
    """
    palette = PALETTES[mode]
    fig = go.Figure()
    groups = list(dict.fromkeys(data["category"].to_list()))[:MAX_LINE_SERIES]
    for i, group in enumerate(groups):
        part = data.filter(pl.col("category") == group)
        color = palette.categorical[i]
        fig.add_trace(
            go.Scatter(
                x=part["period"].to_list(),
                y=part["value"].to_list(),
                name=group,
                mode="lines+markers",
                line={"color": color, "width": 2},
                marker={"size": 6, "color": color},
                customdata=[
                    [granularity.label(p) if granularity else p.isoformat(), fmt_compact(v)]
                    for p, v in zip(part["period"].to_list(), part["value"].to_list(), strict=True)
                ],
                hovertemplate=f"{group}<br>%{{customdata[0]}}<br>{label}: %{{customdata[1]}}"
                "<extra></extra>",
            )
        )
    fig = _style(fig, palette, height=400)
    fig.update_layout(
        showlegend=True,
        legend={"orientation": "h", "y": -0.15, "x": 0},
        yaxis_title=label,
        hovermode="closest",
    )
    return fig


def spread(
    data: pl.DataFrame,
    measure: str,
    order: list[str],
    mode: Mode = "light",
    *,
    log: bool = False,
) -> go.Figure:
    """Horizontal box plots per group (median, IQR, outliers), highest median on top.

    ``log`` uses a log axis so a few huge values don't flatten every box; a log axis
    cannot show zero or negatives, so those rows are left out of the boxes.
    Plotly's built-in box hover prints every statistic as a separate label, which
    overlaps on horizontal boxes, so each group gets one summary tooltip instead.
    """
    palette = PALETTES[mode]
    fig = go.Figure()
    for group in reversed(order):
        series = data.filter(pl.col("category") == group)[measure]
        if log:
            series = series.filter(series > 0)
        if series.is_empty():
            continue
        values = series.to_list()
        fig.add_trace(
            go.Box(
                x=values,
                name=group,
                orientation="h",
                marker={"color": palette.series, "size": 4},
                line={"width": 1.5},
                fillcolor="rgba(42,120,214,0.25)",
                boxpoints="outliers",
                hoverinfo="skip",
                showlegend=False,
            )
        )
        q1, median, q3 = (series.quantile(q, "linear") for q in (0.25, 0.5, 0.75))
        fig.add_trace(
            go.Scatter(
                x=[median],
                y=[group],
                mode="markers",
                marker={"size": 22, "color": "rgba(0,0,0,0)"},  # invisible hover target
                showlegend=False,
                hovertemplate=(
                    f"<b>{group}</b><br>median: {fmt_compact(median)}<br>"
                    f"middle 50%: {fmt_compact(q1)} to {fmt_compact(q3)}<br>"
                    f"{series.len():,} rows<extra></extra>"
                ),
            )
        )
    fig.update_layout(xaxis_title=f"{measure} (log scale)" if log else measure)
    fig.update_yaxes(automargin=True)
    if log:
        fig.update_xaxes(type="log")
    return _style(fig, palette, height=max(260, 40 * len(order) + 80))


def scatter(data: pl.DataFrame, x: str, y: str, mode: Mode = "light") -> go.Figure:
    """Points plus a least-squares line; up to three colored groups and a gray "Other"."""
    palette = PALETTES[mode]
    fig = go.Figure()
    if "group" in data.columns:
        groups = list(dict.fromkeys(data["group"].to_list()))
        named = [g for g in groups if g != OTHER][:MAX_SCATTER_GROUPS]
        ordered = named + ([OTHER] if OTHER in groups else [])
        for i, group in enumerate(ordered):
            part = data.filter(pl.col("group") == group)
            color = palette.muted_ink if group == OTHER else palette.categorical[i]
            fig.add_trace(_points(part, x, y, color, group))
        fig.update_layout(showlegend=True, legend={"orientation": "h", "y": -0.2, "x": 0})
    else:
        fig.add_trace(_points(data, x, y, palette.series, "rows"))

    xs, ys = data[x].to_list(), data[y].to_list()
    if len(xs) >= 2 and len(set(xs)) > 1:
        x_mean, y_mean = sum(xs) / len(xs), sum(ys) / len(ys)
        slope = sum((a - x_mean) * (b - y_mean) for a, b in zip(xs, ys, strict=True)) / sum(
            (a - x_mean) ** 2 for a in xs
        )
        lo, hi = min(xs), max(xs)
        fig.add_trace(
            go.Scatter(
                x=[lo, hi],
                y=[y_mean + slope * (lo - x_mean), y_mean + slope * (hi - x_mean)],
                mode="lines",
                name="linear fit",
                line={"color": palette.muted_ink, "width": 1.5, "dash": "dot"},
                hoverinfo="skip",
            )
        )
    fig = _style(fig, palette, height=440)
    fig.update_layout(
        xaxis_title=x,
        yaxis_title=y,
        showlegend="group" in data.columns,
        legend={"orientation": "h", "y": -0.2, "x": 0},
    )
    return fig


def _points(data: pl.DataFrame, x: str, y: str, color: str, name: str) -> go.Scatter:
    return go.Scatter(
        x=data[x].to_list(),
        y=data[y].to_list(),
        mode="markers",
        name=name,
        marker={"size": 7, "color": color, "opacity": 0.6, "line": {"width": 0}},
        hovertemplate=f"{name}<br>{x}: %{{x:,.4~g}}<br>{y}: %{{y:,.4~g}}<extra></extra>",
    )


def missing_values(data: pl.DataFrame, mode: Mode = "light") -> go.Figure:
    """Share of missing values per column, most incomplete on top (0-100%)."""
    palette = PALETTES[mode]
    rows = list(reversed(data.rows(named=True)))
    fig = go.Figure(
        go.Bar(
            x=[r["missing_pct"] for r in rows],
            y=[r["column"] for r in rows],
            orientation="h",
            marker={"color": palette.series, "cornerradius": 4},
            text=[f"{r['missing_pct']:.0f}%" for r in rows],
            textposition="outside",
            cliponaxis=False,
            customdata=[f"{r['missing']:,}" for r in rows],
            hovertemplate="%{y}: %{x:.1f}% missing (%{customdata} rows)<extra></extra>",
        )
    )
    fig.update_layout(bargap=0.3, xaxis={"title": "Missing values (%)", "range": [0, 100]})
    fig.update_yaxes(automargin=True)
    fig = _style(fig, palette, height=max(200, 30 * len(rows) + 80))
    fig.update_layout(margin={"r": 48})  # room for the "97%" label of the longest bar
    return fig


def coverage(series: TrendSeries, mode: Mode = "light") -> go.Figure:
    """Rows per period: shows whether every period has comparable data."""
    palette = PALETTES[mode]
    fig = go.Figure(
        go.Bar(
            x=list(series.periods),
            y=list(series.counts),
            marker={"color": palette.series, "cornerradius": 4},
            customdata=list(series.labels),
            hovertemplate="%{customdata}: %{y:,} rows<extra></extra>",
        )
    )
    fig.update_layout(bargap=0.15, yaxis_title=f"Rows per {series.granularity}")
    return _style(fig, palette, height=280)
