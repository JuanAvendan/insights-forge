"""The "Compare" tab: how do groups differ in size, over time and in spread?"""

from __future__ import annotations

import streamlit as st

from insights_forge import breakdowns, charts
from insights_forge.app.insights_view import AGGREGATIONS, ROW_COUNT
from insights_forge.formatting import sentence_case
from insights_forge.insights import (
    Aggregation,
    candidate_dates,
    candidate_dimensions,
    candidate_measures,
)
from insights_forge.insights.engine import time_axis, total_of
from insights_forge.insights.roles import aggregation_for
from insights_forge.pipeline import Analysis

RANKING, OVER_TIME, SPREAD = "Ranking", "Over time", "Spread"

VIEW_HELP = {
    RANKING: "Which groups weigh the most? Groups beyond the top 15 are folded into Other.",
    OVER_TIME: "How does each of the 5 largest groups evolve? Incomplete trailing periods are "
    "left out.",
    SPREAD: "Is the gap between groups consistent or driven by a few rows? Boxes span the "
    "middle 50% of rows, the line is the median and dots are outliers.",
}


def measure_label(measure: str | None, agg: Aggregation) -> str:
    if measure is None:
        return "rows"
    return f"average {measure}" if agg is Aggregation.AVG else total_of(measure)


def render_compare(a: Analysis, mode: charts.Mode) -> None:
    dims = candidate_dimensions(a.profile)
    if not dims:
        st.info(
            "Comparisons need a categorical column with 2 to 50 distinct values "
            "(for example region, product or channel)."
        )
        return

    roles = a.insights.roles
    measures = [ROW_COUNT, *candidate_measures(a.profile)]
    left, middle, right = st.columns([3, 3, 2])
    dim = left.selectbox("Compare by", dims, key="cmp_dim")
    picked = middle.selectbox(
        "Measure",
        measures,
        index=measures.index(roles.measure) if roles.measure in measures else 0,
        key="cmp_measure",
    )
    agg_label = right.selectbox(
        "Aggregation", list(AGGREGATIONS), key="cmp_agg", disabled=picked == ROW_COUNT
    )

    measure = None if picked == ROW_COUNT else picked
    override = AGGREGATIONS[agg_label]
    agg = Aggregation.COUNT if measure is None else override or aggregation_for(measure)
    label = measure_label(measure, agg)

    dates = candidate_dates(a.profile)
    date = roles.date or (dates[0] if dates else None)
    views = [RANKING, *([OVER_TIME] if date else []), *([SPREAD] if measure else [])]
    view = st.segmented_control("View", views, default=RANKING, key="cmp_view") or RANKING
    st.caption(VIEW_HELP[view])

    if view == RANKING:
        data = breakdowns.rank_categories(a.df, dim, measure, agg)
        st.markdown(f"**{sentence_case(label)} by {dim}**")
        st.plotly_chart(charts.ranking(data, label, mode), width="stretch")
    elif view == OVER_TIME and date:
        trend = a.insights.trend
        periods = trend.periods if trend is not None and roles.date == date else None
        data = breakdowns.categories_over_time(
            a.df, a.profile, dim, date, measure, agg, periods=periods
        )
        axis = time_axis(a.profile, date)
        st.markdown(f"**{sentence_case(label)} by {dim} over time**")
        st.plotly_chart(
            charts.lines(data, label, mode, axis.granularity if axis else None),
            width="stretch",
        )
        if agg is not Aggregation.AVG and measure is not None:
            entity = trend.entity if trend is not None and roles.date == date else None
            if entity:
                st.caption(
                    f"The number of {entity} values reporting changes between periods, so a "
                    "group's total can jump just because more of its members reported. "
                    "Switch the aggregation to Average to compare like with like."
                )
            else:
                st.caption(
                    "Totals also grow when a group simply has more rows in a period; switch "
                    "the aggregation to Average to compare like with like."
                )
    elif view == SPREAD and measure:
        data, order = breakdowns.spread_by_category(a.df, dim, measure)
        values = data[measure]
        log = st.toggle(
            "Log scale",
            value=breakdowns.is_heavily_skewed(values),
            disabled=not breakdowns.can_use_log(values),
            key="cmp_log",
            help="Spreads out values that span several orders of magnitude so a few huge "
            "rows don't flatten the boxes. Needs non-negative values.",
        )
        st.markdown(f"**Distribution of {measure} by {dim}**")
        st.plotly_chart(charts.spread(data, measure, order, mode, log=log), width="stretch")
        hidden = breakdowns.non_positive_count(values) if log else 0
        if hidden:
            st.caption(
                f"{hidden:,} rows with {measure} = 0 cannot be drawn on a log scale and are "
                "left out of the boxes; turn the log scale off to include them."
            )
