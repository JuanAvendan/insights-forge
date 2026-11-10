"""Render an :class:`Analysis` as a single, shareable HTML file."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import plotly.graph_objects as go
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from insights_forge import __version__, breakdowns, charts
from insights_forge.formatting import fmt_number, sentence_case
from insights_forge.pipeline import Analysis
from insights_forge.profiling import ColumnProfile, Severity

SEVERITY_ICON = {Severity.CRITICAL: "🛑", Severity.WARNING: "⚠️", Severity.INFO: "ℹ️"}  # noqa: RUF001

_env = Environment(
    loader=PackageLoader("insights_forge.report", "templates"),
    autoescape=select_autoescape(["html", "j2"]),
    trim_blocks=True,
    lstrip_blocks=True,
)
_env.filters["thousands"] = lambda n: f"{n:,}"
_env.filters["sentence"] = sentence_case


def column_summary(col: ColumnProfile) -> str:
    """One-line description of a column's values."""
    if col.count == 0:
        return "No values"
    if n := col.numeric:
        return f"median {fmt_number(n.median)} · range {fmt_number(n.min)} to {fmt_number(n.max)}"
    if t := col.temporal:
        return f"{t.min.isoformat()} to {t.max.isoformat()} ({t.span_days:,} days)"
    if col.top_values:
        top = col.top_values[0]
        return f"most common: {top.value} ({100 * top.count / col.count:.0f}%)"
    return ""


def render_html(
    analysis: Analysis,
    *,
    offline: bool = False,
    generated_at: dt.datetime | None = None,
) -> str:
    """Build the report.

    Args:
        offline: embed plotly.js (~4.5 MB) instead of loading it from a CDN, so the
            file also works without an internet connection.
    """
    plotly_js: Literal[True, "cdn"] = True if offline else "cdn"
    embedded = False

    def to_html(fig: go.Figure) -> Markup:
        nonlocal embedded
        html = charts.transparent(fig).to_html(
            full_html=False,
            include_plotlyjs=False if embedded else plotly_js,
            config={"displaylogo": False, "responsive": True},
        )
        embedded = True
        return Markup(html)  # noqa: S704 - Plotly output, not user input

    trend = analysis.insights.trend
    trend_chart = (
        to_html(charts.trend(trend, analysis.insights.roles.measure_label)) if trend else None
    )
    missing = breakdowns.missing_by_column(analysis.profile)
    missing_chart = to_html(charts.missing_values(missing)) if missing.height else None
    roles = analysis.insights.roles
    ranking_chart, ranking_title = None, ""
    if roles.dimensions:
        dim = roles.dimensions[0]
        data = breakdowns.rank_categories(analysis.df, dim, roles.measure, roles.aggregation)
        label = "rows" if roles.measure is None else roles.measure_label
        ranking_chart = to_html(charts.ranking(data, label))
        ranking_title = f"{sentence_case(label)} by {dim}"
    correlation_chart = (
        to_html(charts.correlation_heatmap(analysis.profile))
        if analysis.profile.correlations
        else None
    )

    return _env.get_template("report.html.j2").render(
        name=analysis.name,
        version=__version__,
        generated_at=(generated_at or dt.datetime.now()).strftime("%Y-%m-%d %H:%M"),
        profile=analysis.profile,
        insights=analysis.insights,
        trend_chart=trend_chart,
        correlation_chart=correlation_chart,
        missing_chart=missing_chart,
        ranking_chart=ranking_chart,
        ranking_title=ranking_title,
        severity_icon=SEVERITY_ICON,
        column_summary=column_summary,
    )


def write_html(analysis: Analysis, path: Path, *, offline: bool = False) -> Path:
    path.write_text(render_html(analysis, offline=offline), encoding="utf-8")
    return path
