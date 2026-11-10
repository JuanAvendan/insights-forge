"""The "Insights" tab: role pickers, trend chart, ranked findings and report download."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from insights_forge import charts
from insights_forge.formatting import sentence_case
from insights_forge.insights import (
    Aggregation,
    InsightKind,
    InsightReport,
    candidate_dates,
    candidate_measures,
)
from insights_forge.pipeline import Analysis, regenerate_insights
from insights_forge.report import render_html

ROW_COUNT = "(row count)"
NO_DATE = "(none)"
AGGREGATIONS: dict[str, Aggregation | None] = {
    "Auto": None,
    "Sum": Aggregation.SUM,
    "Average": Aggregation.AVG,
}

KIND_ICON = {
    InsightKind.TREND: "📈",
    InsightKind.PEAK: "⛰️",
    InsightKind.CONCENTRATION: "🎯",
    InsightKind.SEGMENT: "🧮",
    InsightKind.CORRELATION: "🔗",
    InsightKind.DISTRIBUTION: "📊",
    InsightKind.QUALITY: "🧹",
}


@st.cache_data(show_spinner="Generating insights...", max_entries=20)
def cached_insights(
    source_key: str,
    measure: str | None,
    date: str | None,
    aggregation: Aggregation | None,
    _analysis: Analysis,
) -> InsightReport:
    # `_analysis` is not hashed by Streamlit; `source_key` identifies the dataset.
    return regenerate_insights(_analysis, measure=measure, date=date, aggregation=aggregation)


@st.cache_data(show_spinner="Building report...", max_entries=20)
def cached_report(
    source_key: str,
    measure: str | None,
    date: str | None,
    aggregation: Aggregation | None,
    _analysis: Analysis,
) -> str:
    return render_html(_analysis)


def _pick_roles(a: Analysis) -> tuple[str | None, str | None, Aggregation | None]:
    default = a.insights.roles
    measures = [ROW_COUNT, *candidate_measures(a.profile)]
    dates = [NO_DATE, *candidate_dates(a.profile)]
    left, middle, right = st.columns([3, 2, 3])
    measure = left.selectbox(
        "Measure",
        measures,
        index=measures.index(default.measure) if default.measure else 0,
        key="measure",
        help="Numeric column the insights focus on. Auto-detected; change it to explore.",
    )
    agg_label = middle.selectbox(
        "Aggregation",
        list(AGGREGATIONS),
        key="aggregation",
        disabled=measure == ROW_COUNT,
        help="Sum suits amounts (sales, budget); average suits prices, rates and scores. "
        "Auto decides from the column name.",
    )
    date = right.selectbox(
        "Date column",
        dates,
        index=dates.index(default.date) if default.date else 0,
        key="date",
        help="Used for trends over time.",
    )
    return (
        None if measure == ROW_COUNT else measure,
        None if date == NO_DATE else date,
        AGGREGATIONS[agg_label],
    )


def render_insights(a: Analysis, source_key: str, mode: charts.Mode) -> None:
    measure, date, aggregation = _pick_roles(a)
    roles = a.insights.roles
    if (measure, date, aggregation) == (roles.measure, roles.date, None):
        report = a.insights
    else:
        report = cached_insights(source_key, measure, date, aggregation, a)
    current = Analysis(name=a.name, df=a.df, profile=a.profile, insights=report)

    if report.trend is not None:
        label = report.roles.measure_label
        st.markdown(f"**{sentence_case(label)} by {report.trend.granularity}**")
        st.plotly_chart(charts.trend(report.trend, label, mode), width="stretch")
        st.caption("Dotted line: least-squares linear trend.")

    st.markdown(f"#### {len(report.insights)} key insights")
    if not report.insights:
        st.info("No notable patterns were found with the current settings.")
    for insight in report.insights:
        with st.container(border=True):
            st.markdown(f"{KIND_ICON[insight.kind]} **{insight.title}**")
            st.caption(insight.detail)

    st.download_button(
        "⬇️ Download HTML report",
        data=cached_report(source_key, measure, date, aggregation, current),
        file_name=f"{Path(a.name).stem}.report.html",
        mime="text/html",
        help="A single shareable file with these insights, data quality and charts.",
    )
    st.caption("Insights are rule-based heuristics: review them before making decisions.")
