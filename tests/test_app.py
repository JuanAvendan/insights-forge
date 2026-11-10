"""Smoke tests for the Streamlit UI using Streamlit's headless AppTest runner."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "src" / "insights_forge" / "app" / "streamlit_app.py"


def test_app_starts_without_errors() -> None:
    at = AppTest.from_file(str(APP)).run()
    assert not at.exception
    assert at.title[0].value == "🔨 insights-forge"


def test_sample_data_button_loads_dataset() -> None:
    at = AppTest.from_file(str(APP)).run()
    at.sidebar.button[0].click().run(timeout=30)
    assert not at.exception
    assert at.subheader[0].value == "sales.csv"
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Rows"] == "1,012"
    assert metrics["Columns"] == "11"
    assert metrics["Duplicate rows"] == "24"  # 12 duplicated rows -> 24 rows flagged
