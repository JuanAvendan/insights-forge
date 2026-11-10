"""End-to-end analysis: load a source, profile it and generate insights.

Shared by the web app, the CLI and the HTML report so they all produce the
same results.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from insights_forge.connectors import Connector, FileConnector
from insights_forge.insights import InsightReport, generate_insights
from insights_forge.profiling import DatasetProfile, profile_table

TABLE = "dataset"


@dataclass(frozen=True, slots=True)
class Analysis:
    name: str
    df: pl.DataFrame
    profile: DatasetProfile
    insights: InsightReport


def analyze(
    connector: Connector,
    name: str,
    *,
    measure: str | None = None,
    date: str | None = None,
) -> Analysis:
    con = duckdb.connect()
    try:
        relation = connector.load(con, TABLE)
        profile = profile_table(con, TABLE)
        insights = generate_insights(con, profile, measure=measure, date=date)
        df: pl.DataFrame = relation.pl()  # duckdb types .pl() as Any
    finally:
        con.close()
    return Analysis(name=name, df=df, profile=profile, insights=insights)


def analyze_file(
    path: Path,
    *,
    sheet: str | None = None,
    measure: str | None = None,
    date: str | None = None,
) -> Analysis:
    return analyze(FileConnector(path, sheet=sheet), path.name, measure=measure, date=date)


def regenerate_insights(
    analysis: Analysis,
    *,
    measure: str | None = None,
    date: str | None = None,
) -> InsightReport:
    """Re-run the insight engine with different roles, reusing the loaded data."""
    con = duckdb.connect()
    try:
        con.register("_source", analysis.df)
        con.execute(f'CREATE TABLE "{TABLE}" AS FROM _source')
        con.unregister("_source")
        return generate_insights(con, analysis.profile, measure=measure, date=date)
    finally:
        con.close()
