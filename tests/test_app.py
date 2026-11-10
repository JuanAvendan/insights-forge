"""Smoke tests for the Streamlit UI using Streamlit's headless AppTest runner."""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "src" / "insights_forge" / "app" / "streamlit_app.py"


@pytest.fixture
def sample_app() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.button[0].click().run()
    assert not at.exception
    return at


def test_app_starts_without_errors() -> None:
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert at.title[0].value == "🔨 insights-forge"


def test_sample_data_overview(sample_app: AppTest) -> None:
    assert sample_app.subheader[0].value == "sales.csv"
    metrics = {m.label: m.value for m in sample_app.metric}
    assert metrics["Rows"] == "1,012"
    assert metrics["Columns"] == "11"
    assert metrics["Duplicates"] == "12"
    assert int(metrics["Issues"]) >= 6


def test_sample_data_tabs(sample_app: AppTest) -> None:
    labels = [t.label for t in sample_app.tabs]
    assert labels[:2] == ["Insights", "Overview"]
    assert labels[2].startswith("Data quality (")
    assert labels[3:] == ["Columns", "Correlations", "Preview"]


def test_column_explorer_switches_column(sample_app: AppTest) -> None:
    sample_app.selectbox(key="column").select("region").run()
    assert not sample_app.exception
    sample_app.selectbox(key="column").select("order_date").run()
    assert not sample_app.exception


def test_outlier_toggle_on_numeric_column(sample_app: AppTest) -> None:
    sample_app.selectbox(key="column").select("quantity").run()
    toggle = sample_app.toggle[0]
    assert toggle.label.startswith("Hide 11 extreme outliers")
    toggle.set_value(True).run()
    assert not sample_app.exception


def test_sample_survives_rerun(sample_app: AppTest) -> None:
    """The sample stays loaded after unrelated interactions (stored in session state)."""
    sample_app.run()
    assert sample_app.subheader[0].value == "sales.csv"


def test_insights_tab_defaults(sample_app: AppTest) -> None:
    assert sample_app.selectbox(key="measure").value == "total"
    assert sample_app.selectbox(key="date").value == "order_date"
    titles = [m.value for m in sample_app.markdown]
    assert any("key insights" in t for t in titles)
    assert any("Duplicate rows inflate total" in t for t in titles)


def test_changing_measure_regenerates_insights(sample_app: AppTest) -> None:
    sample_app.selectbox(key="measure").select("quantity").run()
    assert not sample_app.exception
    titles = " ".join(m.value for m in sample_app.markdown)
    assert "Quantity by month" in titles


def test_row_count_without_date(sample_app: AppTest) -> None:
    sample_app.selectbox(key="measure").select("(row count)").run()
    sample_app.selectbox(key="date").select("(none)").run()
    assert not sample_app.exception
