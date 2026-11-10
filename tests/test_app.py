"""Smoke tests for the Streamlit UI using Streamlit's headless AppTest runner."""

from __future__ import annotations

import os
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
    assert labels[:3] == ["Insights", "Compare", "Overview"]
    assert labels[3].startswith("Data quality (")
    assert labels[4:] == ["Columns", "Correlations", "Preview"]


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


# --------------------------------------------------------------------------- databases


def _database_app(monkeypatch: pytest.MonkeyPatch, url: str | None) -> AppTest:
    if url is None:
        monkeypatch.delenv("INSIGHTS_FORGE_DB_URL", raising=False)
    else:
        monkeypatch.setenv("INSIGHTS_FORGE_DB_URL", url)
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.segmented_control(key="source_kind").set_value("Database").run()
    return at


def _connect(at: AppTest) -> AppTest:
    next(b for b in at.sidebar.button if b.label == "Connect").click().run()
    return at


def test_database_table_via_environment(shop_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _connect(_database_app(monkeypatch, f"sqlite:///{shop_db.as_posix()}"))
    assert not at.exception
    assert at.sidebar.selectbox(key="db_table").options == ["revenue_by_region", "sales"]
    at.sidebar.selectbox(key="db_table").select("sales").run()
    assert at.subheader[0].value.startswith("sales (sqlite:///")
    assert {m.label: m.value for m in at.metric}["Rows"] == "1,012"


def test_database_query(shop_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _connect(_database_app(monkeypatch, f"sqlite:///{shop_db.as_posix()}"))
    at.sidebar.radio(key="db_mode").set_value("Query").run()
    at.sidebar.text_area(key="db_query").input("SELECT region, total FROM sales").run()
    assert not at.exception
    assert at.subheader[0].value.startswith("query (")


def test_database_blocked_query_shows_error(shop_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    at = _connect(_database_app(monkeypatch, f"sqlite:///{shop_db.as_posix()}"))
    at.sidebar.radio(key="db_mode").set_value("Query").run()
    at.sidebar.text_area(key="db_query").input("DELETE FROM sales").run()
    assert "Only read queries" in at.error[0].value


def test_database_form_validates_port(monkeypatch: pytest.MonkeyPatch) -> None:
    at = _database_app(monkeypatch, None)
    at.sidebar.text_input[1].input("abc")  # Port
    _connect(at)
    assert "Port must be a number" in at.sidebar.error[0].value


# --------------------------------------------------------------------------- URLs & MongoDB


def _url_app() -> AppTest:
    at = AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.segmented_control(key="source_kind").set_value("URL").run()
    return at


def test_url_source_refuses_internal_addresses() -> None:
    at = _url_app()
    at.sidebar.text_input(key="remote_url").input("http://169.254.169.254/latest/x.csv").run()
    assert "internal address" in at.error[0].value


def test_url_source_rejects_local_paths() -> None:
    at = _url_app()
    at.sidebar.text_input(key="remote_url").input("C:/data/sales.csv").run()
    assert "Enter an https://" in at.sidebar.error[0].value


def test_url_source_loads_a_download(monkeypatch: pytest.MonkeyPatch) -> None:
    sample = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"

    def fake_download(url: str, directory: Path, **kwargs: object) -> Path:
        assert kwargs["allow_private"] is False  # the app always keeps SSRF protection on
        target = directory / "sales.csv"
        target.write_bytes(sample.read_bytes())
        return target

    monkeypatch.setattr("insights_forge.connectors.remote.download", fake_download)
    at = _url_app()
    at.sidebar.text_input(key="remote_url").input("https://example.org/app-test/sales.csv").run()
    assert not at.exception
    assert at.subheader[0].value == "sales.csv"


@pytest.mark.integration
def test_mongodb_source(monkeypatch: pytest.MonkeyPatch) -> None:
    url = os.environ.get("INSIGHTS_FORGE_TEST_MONGO_URL")
    if not url:
        pytest.skip("INSIGHTS_FORGE_TEST_MONGO_URL not set")
    at = _connect(_database_app(monkeypatch, url))
    assert not at.exception
    assert at.sidebar.selectbox(key="mongo_collection").options == ["sales"]
    assert at.subheader[0].value.startswith("sales (mongodb://analyst:***@")
    at.sidebar.text_area(key="mongo_filter").input('{"region": "East"}').run()
    assert {m.label: m.value for m in at.metric}["Rows"] == "312"
