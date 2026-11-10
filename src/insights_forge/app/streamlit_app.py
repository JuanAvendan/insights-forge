"""Streamlit UI.

Run with:  uv run streamlit run src/insights_forge/app/streamlit_app.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import duckdb
import polars as pl
import streamlit as st

from insights_forge import __version__
from insights_forge.connectors import SUPPORTED_EXTENSIONS, ConnectorError, FileConnector

SAMPLE_FILE = Path(__file__).resolve().parents[3] / "data" / "sample" / "sales.csv"
PREVIEW_ROWS = 100


def load_file(path: Path) -> pl.DataFrame:
    con = duckdb.connect()
    try:
        df: pl.DataFrame = FileConnector(path).load(con).pl()  # duckdb types .pl() as Any
        return df
    finally:
        con.close()


@st.cache_data(show_spinner="Loading data...")
def load_upload(name: str, data: bytes) -> pl.DataFrame:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / Path(name).name
        path.write_bytes(data)
        return load_file(path)


def column_summary(df: pl.DataFrame) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "column": df.columns,
            "type": [str(dtype) for dtype in df.dtypes],
            "nulls": [df[col].null_count() for col in df.columns],
            "unique": [df[col].n_unique() for col in df.columns],
        }
    ).with_columns(null_pct=(pl.col("nulls") / max(df.height, 1) * 100).round(2))


def render_dataset(df: pl.DataFrame, source_name: str) -> None:
    st.subheader(source_name)
    rows, cols, dups = st.columns(3)
    rows.metric("Rows", f"{df.height:,}")
    cols.metric("Columns", df.width)
    dups.metric("Duplicate rows", f"{df.is_duplicated().sum():,}")

    st.markdown("#### Columns")
    st.dataframe(column_summary(df), hide_index=True, use_container_width=True)

    st.markdown(f"#### Preview (first {PREVIEW_ROWS} rows)")
    st.dataframe(df.head(PREVIEW_ROWS), use_container_width=True)

    st.info("Automatic profiling and insights are coming in the next release.", icon="🛠️")


def main() -> None:
    st.set_page_config(page_title="insights-forge", page_icon="🔨", layout="wide")
    st.title("🔨 insights-forge")
    st.caption(f"Load data from any source and get automatic insights · v{__version__}")

    with st.sidebar:
        st.header("Data source")
        uploaded = st.file_uploader("Upload a file", type=list(SUPPORTED_EXTENSIONS))
        use_sample = st.button("Try with sample data", disabled=not SAMPLE_FILE.exists())

    try:
        if uploaded is not None:
            render_dataset(load_upload(uploaded.name, uploaded.getvalue()), uploaded.name)
        elif use_sample:
            render_dataset(load_file(SAMPLE_FILE), SAMPLE_FILE.name)
        else:
            st.write("Upload a file or load the sample dataset from the sidebar to get started.")
    except ConnectorError as exc:
        st.error(f"Could not load the data: {exc}")


main()
