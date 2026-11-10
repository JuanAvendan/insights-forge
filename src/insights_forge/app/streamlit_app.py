"""Streamlit UI.

Run with:  uv run streamlit run src/insights_forge/app/streamlit_app.py
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import polars as pl
import streamlit as st

from insights_forge import __version__, breakdowns, charts
from insights_forge.app.compare_view import render_compare
from insights_forge.app.insights_view import render_insights
from insights_forge.connectors import SUPPORTED_EXTENSIONS, ConnectorError
from insights_forge.formatting import fmt_number
from insights_forge.insights import candidate_dimensions
from insights_forge.pipeline import Analysis, analyze_file
from insights_forge.profiling import ColumnKind, ColumnProfile, DatasetProfile, Issue, Severity

SAMPLE_FILE = Path(__file__).resolve().parents[3] / "data" / "sample" / "sales.csv"
REPO_URL = "https://github.com/JuanAvendan/insights-forge"
PREVIEW_ROWS = 100

# Severity always ships with an icon + word, never color alone.
SEVERITY_LABEL = {
    Severity.CRITICAL: "🛑 Critical",
    Severity.WARNING: "⚠️ Warning",
    Severity.INFO: "ℹ️ Info",  # noqa: RUF001 - the info emoji is intentional
}
KIND_ICON = {
    ColumnKind.NUMERIC: "🔢",
    ColumnKind.TEXT: "🔤",
    ColumnKind.TEMPORAL: "📅",
    ColumnKind.BOOLEAN: "☑️",
    ColumnKind.OTHER: "🧩",
}


# --------------------------------------------------------------------------- loading


@st.cache_data(show_spinner="Analyzing data...", max_entries=5)
def analyze_upload(name: str, data: bytes) -> Analysis:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        return analyze_file(path)


@st.cache_data(show_spinner="Analyzing sample data...")
def analyze_sample() -> Analysis:
    return analyze_file(SAMPLE_FILE)


def theme_mode() -> charts.Mode:
    return "dark" if getattr(st.context.theme, "type", None) == "dark" else "light"


# --------------------------------------------------------------------------- views


ISSUE_COLUMNS = {"Issue": st.column_config.TextColumn("Issue", width="large")}


def issues_table(issues: tuple[Issue, ...] | list[Issue]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "Severity": [SEVERITY_LABEL[i.severity] for i in issues],
            "Column": [i.column or "(whole table)" for i in issues],
            "Issue": [i.message for i in issues],
            "Rows affected": [i.affected_rows or None for i in issues],
        },
        schema={
            "Severity": pl.String,
            "Column": pl.String,
            "Issue": pl.String,
            "Rows affected": pl.Int64,
        },
    )


def columns_table(profile: DatasetProfile) -> pl.DataFrame:
    issue_counts: dict[str, int] = {}
    for issue in profile.issues:
        if issue.column:
            issue_counts[issue.column] = issue_counts.get(issue.column, 0) + 1
    return pl.DataFrame(
        {
            "Column": [f"{KIND_ICON[c.kind]} {c.name}" for c in profile.columns],
            "Type": [c.dtype for c in profile.columns],
            "Filled": [100 - c.null_pct for c in profile.columns],
            "Distinct": [c.distinct for c in profile.columns],
            "Issues": [issue_counts.get(c.name, 0) for c in profile.columns],
        }
    )


def render_overview(a: Analysis) -> None:
    p = a.profile
    critical = len(p.issues_by_severity(Severity.CRITICAL))
    warnings = len(p.issues_by_severity(Severity.WARNING))
    info = len(p.issues_by_severity(Severity.INFO))

    m = st.columns(5)
    m[0].metric("Rows", f"{p.rows:,}")
    m[1].metric("Columns", p.n_columns)
    m[2].metric("Complete", f"{p.completeness_pct:.1f}%", help="Share of non-missing cells.")
    m[3].metric("Duplicates", f"{p.duplicate_rows:,}", help="Extra copies of an identical row.")
    m[4].metric(
        "Issues",
        len(p.issues),
        help=f"{critical} critical, {warnings} warnings, {info} info.",
    )

    st.markdown("#### Columns")
    st.dataframe(
        columns_table(p),
        hide_index=True,
        width="stretch",
        column_config={
            "Filled": st.column_config.ProgressColumn(
                "Filled", format="%.1f%%", min_value=0, max_value=100
            ),
        },
    )

    if p.issues:
        st.markdown("#### Top issues")
        st.dataframe(
            issues_table(p.issues[:5]),
            hide_index=True,
            width="stretch",
            column_config=ISSUE_COLUMNS,
        )


def render_quality(a: Analysis) -> None:
    issues = a.profile.issues
    if not issues:
        st.success("No data-quality issues found.", icon="✅")
        return

    mode = theme_mode()
    missing = breakdowns.missing_by_column(a.profile)
    trend = a.insights.trend
    left, right = st.columns(2, gap="large")
    if missing.height:
        with left:
            st.markdown("**Where is data missing?**")
            st.plotly_chart(charts.missing_values(missing, mode), width="stretch")
    if trend is not None and trend.counts:
        with right:
            st.markdown(f"**Rows per {trend.granularity}**")
            st.plotly_chart(charts.coverage(trend, mode), width="stretch")
            low, high = min(trend.counts), max(trend.counts)
            if trend.entity is None:
                st.caption("Data volume per period. Gaps or sudden drops can reveal missing data.")
            elif high and low / high < 0.8:
                st.caption(
                    f"Coverage varies: {low:,} to {high:,} {trend.entity} values per "
                    f"{trend.granularity}. Compare averages rather than totals across periods."
                )
            else:
                st.caption(
                    f"Coverage is even: every {trend.granularity} has similar {trend.entity}s."
                )

    options = [s for s in SEVERITY_LABEL if any(i.severity is s for i in issues)]
    selected = st.pills(
        "Severity",
        options,
        selection_mode="multi",
        default=options,
        format_func=lambda s: SEVERITY_LABEL[s],
    )
    shown = [i for i in issues if i.severity in selected]
    st.dataframe(issues_table(shown), hide_index=True, width="stretch", column_config=ISSUE_COLUMNS)
    st.caption(f"Showing {len(shown)} of {len(issues)} issues.")


def column_stats(col: ColumnProfile) -> list[tuple[str, str]]:
    stats = [
        ("Type", col.dtype),
        ("Non-null", f"{col.count:,}"),
        ("Missing", f"{col.nulls:,} ({col.null_pct:.1f}%)"),
        ("Distinct", f"{col.distinct:,} ({col.distinct_pct:.1f}%)"),
    ]
    if n := col.numeric:
        stats += [
            ("Min", fmt_number(n.min)),
            ("Q1", fmt_number(n.q1)),
            ("Median", fmt_number(n.median)),
            ("Mean", fmt_number(n.mean)),
            ("Q3", fmt_number(n.q3)),
            ("Max", fmt_number(n.max)),
            ("Std. dev.", fmt_number(n.std)),
            ("Zeros", f"{n.zeros:,}"),
            ("Negatives", f"{n.negatives:,}"),
            ("Extreme outliers", f"{n.outliers:,}"),
        ]
    if t := col.text:
        stats += [
            ("Length (min / mean / max)", f"{t.min_length} / {t.mean_length:.1f} / {t.max_length}"),
            ("Blank", f"{t.blank:,}"),
            ("Untrimmed", f"{t.padded:,}"),
        ]
    if d := col.temporal:
        stats += [
            ("From", d.min.isoformat()),
            ("To", d.max.isoformat()),
            ("Span", f"{d.span_days:,} days"),
        ]
    return stats


def render_columns(a: Analysis) -> None:
    p = a.profile
    name = st.selectbox(
        "Column",
        [c.name for c in p.columns],
        format_func=lambda n: f"{KIND_ICON[p.column(n).kind]} {n}",
        key="column",
    )
    if name is None:
        return
    col = p.column(name)
    mode = theme_mode()

    left, right = st.columns([2, 3], gap="large")
    with left:
        st.dataframe(
            pl.DataFrame(column_stats(col), schema=["Statistic", "Value"], orient="row"),
            hide_index=True,
            width="stretch",
        )
    with right:
        if col.count == 0:
            st.info("This column has no values to chart.")
        elif col.kind is ColumnKind.NUMERIC:
            st.markdown("**Distribution**")
            stats = col.numeric
            hide_outliers = bool(stats and stats.outliers) and st.toggle(
                f"Hide {stats.outliers:,} extreme outliers" if stats else "",
                help="Zoom into the bulk of the data by excluding values beyond the IQR fences.",
            )
            fig = charts.distribution(
                a.df,
                name,
                mode,
                value_range=(stats.lower_fence, stats.upper_fence)
                if stats and hide_outliers
                else None,
            )
            st.plotly_chart(fig, width="stretch")
        elif col.kind is ColumnKind.TEMPORAL:
            st.markdown("**Rows over time**")
            st.plotly_chart(charts.over_time(a.df, name, mode), width="stretch")
        elif col.top_values:
            st.markdown(f"**Top {len(col.top_values)} values**")
            st.plotly_chart(charts.top_values(col, mode), width="stretch")
            if col.distinct_pct > 50:
                st.caption(
                    "Mostly unique values (an identifier or free text), so there are no "
                    "dominant categories. The most repeated values are shown."
                )
        if a.df.height > charts.MAX_CHART_ROWS and col.kind in {
            ColumnKind.NUMERIC,
            ColumnKind.TEMPORAL,
        }:
            st.caption(f"Chart based on a random sample of {charts.MAX_CHART_ROWS:,} rows.")

    column_issues = [i for i in p.issues if i.column == name]
    if column_issues:
        st.markdown("**Issues in this column**")
        st.dataframe(
            issues_table(column_issues),
            hide_index=True,
            width="stretch",
            column_config=ISSUE_COLUMNS,
        )


def render_correlations(a: Analysis) -> None:
    p = a.profile
    if not p.correlations:
        st.info("Correlations need at least two numeric columns that vary.")
        return

    st.plotly_chart(charts.correlation_heatmap(p, theme_mode()), width="stretch")
    st.caption(
        "Pearson correlation measures linear association: +1 move together, "
        "-1 move in opposite directions, 0 no linear relation. Correlation is not causation."
    )
    notable = [c for c in p.correlations if c.strength != "negligible"]
    if notable:
        st.markdown("#### Notable relationships")
        st.dataframe(
            pl.DataFrame(
                {
                    "Column A": [c.column_a for c in notable],
                    "Column B": [c.column_b for c in notable],
                    "r": [round(c.pearson, 3) for c in notable],
                    "Strength": [c.strength for c in notable],
                }
            ),
            hide_index=True,
            width="stretch",
        )


def render_scatter_explorer(a: Analysis) -> None:
    p = a.profile
    numeric = [c.name for c in p.columns_of(ColumnKind.NUMERIC) if c.distinct > 1]
    if len(numeric) < 2 or not p.correlations:  # pragma: no cover - guarded by caller
        return
    st.markdown("#### Explore a relationship")
    st.caption(
        "A scatter plot shows whether a correlation holds across the data or is driven by a "
        "few extreme points."
    )
    strongest = p.correlations[0]
    left, middle, right = st.columns(3)
    x = left.selectbox("X axis", numeric, index=numeric.index(strongest.column_a), key="sc_x")
    y_options = [n for n in numeric if n != x]
    default_y = strongest.column_b if strongest.column_b in y_options else y_options[0]
    y = middle.selectbox("Y axis", y_options, index=y_options.index(default_y), key="sc_y")
    color = right.selectbox("Color by", ["(none)", *candidate_dimensions(p)], key="sc_color")

    points = breakdowns.scatter_points(a.df, x, y, None if color == "(none)" else color)
    st.plotly_chart(charts.scatter(points, x, y, theme_mode()), width="stretch")
    r = next(
        (c.pearson for c in p.correlations if {c.column_a, c.column_b} == {x, y}),
        None,
    )
    note = f"r = {r:.2f} · " if r is not None else ""
    sampled = " (random sample)" if points.height >= breakdowns.MAX_SCATTER_POINTS else ""
    st.caption(f"{note}{points.height:,} rows with both values{sampled}. Dotted line: linear fit.")


def render_analysis(a: Analysis, source_key: str) -> None:
    st.subheader(a.name)
    insights, compare, overview, quality, columns, correlations, preview = st.tabs(
        [
            "Insights",
            "Compare",
            "Overview",
            f"Data quality ({len(a.profile.issues)})",
            "Columns",
            "Correlations",
            "Preview",
        ]
    )
    with insights:
        render_insights(a, source_key, theme_mode())
    with compare:
        render_compare(a, theme_mode())
    with overview:
        render_overview(a)
    with quality:
        render_quality(a)
    with columns:
        render_columns(a)
    with correlations:
        render_correlations(a)
        if a.profile.correlations:
            render_scatter_explorer(a)
    with preview:
        st.dataframe(a.df.head(PREVIEW_ROWS), width="stretch")
        st.caption(f"First {min(PREVIEW_ROWS, a.df.height):,} of {a.df.height:,} rows.")


# --------------------------------------------------------------------------- page


def main() -> None:
    st.set_page_config(page_title="insights-forge", page_icon="🔨", layout="wide")
    st.title("🔨 insights-forge")
    st.caption(f"Load data from any source and get automatic insights · v{__version__}")

    with st.sidebar:
        st.header("Data source")
        uploaded = st.file_uploader(
            "Upload a file",
            type=list(SUPPORTED_EXTENSIONS),
            on_change=lambda: st.session_state.update(source="upload"),
        )
        if st.button("Try with sample data", disabled=not SAMPLE_FILE.exists()):
            st.session_state.source = "sample"
        st.divider()
        st.caption(f"[Source code on GitHub]({REPO_URL})")

    source = st.session_state.get("source")
    try:
        if source == "sample":
            render_analysis(analyze_sample(), source_key="sample")
        elif uploaded is not None:
            data = uploaded.getvalue()
            key = f"{uploaded.name}:{hashlib.sha256(data).hexdigest()}"
            render_analysis(analyze_upload(uploaded.name, data), source_key=key)
        else:
            st.write("Upload a file or load the sample dataset from the sidebar to get started.")
    except ConnectorError as exc:
        st.error(f"Could not load the data: {exc}")


if __name__ == "__main__":  # Streamlit runs the script as __main__
    main()
