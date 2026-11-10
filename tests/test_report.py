from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from insights_forge.pipeline import Analysis, analyze_file, regenerate_insights
from insights_forge.profiling import ColumnKind, ColumnProfile
from insights_forge.report import render_html, write_html
from insights_forge.report.html import column_summary

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


@pytest.fixture(scope="module")
def analysis() -> Analysis:
    return analyze_file(SAMPLE)


def test_pipeline_produces_everything(analysis: Analysis) -> None:
    assert analysis.name == "sales.csv"
    assert analysis.df.height == analysis.profile.rows == 1012
    assert analysis.insights.roles.measure == "total"


def test_regenerate_insights_with_other_roles(analysis: Analysis) -> None:
    report = regenerate_insights(analysis, measure="quantity", date="order_date")
    assert report.roles.measure == "quantity"
    assert report.trend is not None


def test_report_contains_key_sections(analysis: Analysis) -> None:
    html = render_html(analysis, generated_at=dt.datetime(2026, 1, 2, 3, 4))
    assert html.startswith("<!doctype html>")
    for text in (
        "Key insights",
        "Data quality",
        "Correlations",
        "Duplicate rows inflate total",
        "generated 2026-01-02 03:04",
        "1,012",
    ):
        assert text in html
    assert html.count("cdn.plot.ly") == 1  # plotly.js loaded once, from the CDN


def test_offline_report_embeds_plotly(analysis: Analysis) -> None:
    html = render_html(analysis, offline=True)
    assert "cdn.plot.ly/plotly" not in html
    assert len(html) > 1_000_000


def test_report_escapes_user_content(analysis: Analysis) -> None:
    evil = Analysis(
        name="<script>alert(1)</script>.csv",
        df=analysis.df,
        profile=analysis.profile,
        insights=analysis.insights,
    )
    html = render_html(evil)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_write_html(analysis: Analysis, tmp_path: Path) -> None:
    out = write_html(analysis, tmp_path / "r.html")
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_column_summary_variants(analysis: Analysis) -> None:
    p = analysis.profile
    assert column_summary(p.column("quantity")).startswith("median 2")
    assert column_summary(p.column("order_date")).startswith("2025-01-01 to 2025-12-31")
    assert column_summary(p.column("channel")).startswith("most common:")
    empty = ColumnProfile("x", "DOUBLE", ColumnKind.NUMERIC, count=0, nulls=3, distinct=0)
    assert column_summary(empty) == "No values"
    other = ColumnProfile("y", "INTEGER[]", ColumnKind.OTHER, count=3, nulls=0, distinct=3)
    assert column_summary(other) == ""
