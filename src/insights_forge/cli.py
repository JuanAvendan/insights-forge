"""Command-line interface.

insights-forge summary data/sample/sales.csv
insights-forge report data/sample/sales.csv -o report.html

# databases: the URL comes from --db or, better, the INSIGHTS_FORGE_DB_URL variable
insights-forge tables --db postgresql+psycopg://analyst:analyst@localhost:55432/shop
insights-forge summary --db ... --table sales
insights-forge report  --db ... --query "SELECT * FROM sales WHERE region = 'East'"
insights-forge summary shop.db --table sales       # SQLite file
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from insights_forge import __version__
from insights_forge.connectors import (
    SQLITE_EXTENSIONS,
    Connector,
    ConnectorError,
    FileConnector,
    SQLConnector,
    list_tables,
)
from insights_forge.connectors.sql import DEFAULT_ROW_LIMIT
from insights_forge.insights import Aggregation
from insights_forge.pipeline import Analysis, analyze

DB_URL_ENV = "INSIGHTS_FORGE_DB_URL"


class UsageError(Exception):
    """Invalid combination of command-line options."""


def _add_location_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "path",
        type=Path,
        nargs="?",
        help="data file: CSV, Excel, Parquet, JSON or a SQLite database (.db/.sqlite)",
    )
    parser.add_argument(
        "--db",
        metavar="URL",
        help=f"database URL (SQLAlchemy format). Prefer the {DB_URL_ENV} environment "
        "variable so the password stays out of your shell history",
    )
    parser.add_argument("--schema", help="database schema (PostgreSQL), default: current")


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    _add_location_args(parser)
    parser.add_argument("--table", help="database table or view to analyze")
    parser.add_argument("--query", help="read-only SELECT query to analyze (databases)")
    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_ROW_LIMIT,
        help=f"maximum rows read from a database (default: {DEFAULT_ROW_LIMIT:,})",
    )
    parser.add_argument("--sheet", help="Excel sheet name (default: first sheet)")
    parser.add_argument("--measure", help="numeric column to analyze (default: auto-detect)")
    parser.add_argument("--date-column", help="date column for trends (default: auto-detect)")
    parser.add_argument(
        "--aggregation",
        choices=["sum", "avg"],
        help="how to aggregate the measure per period/group (default: by column meaning)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insights-forge",
        description="Automatic data-quality checks and insights for files and databases.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    summary = sub.add_parser("summary", help="print data-quality issues and insights")
    _add_source_args(summary)

    report = sub.add_parser("report", help="write a shareable HTML report")
    _add_source_args(report)
    report.add_argument(
        "-o", "--output", type=Path, help="output file (default: <source>.report.html)"
    )
    report.add_argument(
        "--offline",
        action="store_true",
        help="embed plotly.js so the report works without internet (+4.5 MB)",
    )

    tables = sub.add_parser("tables", help="list the tables and views of a database")
    _add_location_args(tables)
    return parser


def _database_url(args: argparse.Namespace) -> str | None:
    """URL from a SQLite path, --db or the environment, in that order."""
    path: Path | None = args.path
    if path is not None and path.suffix.lower() in SQLITE_EXTENSIONS:
        if args.db:
            msg = "give either a file or --db, not both"
            raise UsageError(msg)
        if not path.is_file():
            msg = f"SQLite file not found: {path}"
            raise UsageError(msg)
        return f"sqlite:///{path.as_posix()}"
    if path is not None:
        if args.db:
            msg = "give either a file or --db, not both"
            raise UsageError(msg)
        return None
    url: str | None = args.db or os.environ.get(DB_URL_ENV)
    if not url:
        msg = f"give a data file, --db URL or set {DB_URL_ENV}"
        raise UsageError(msg)
    if args.db and _has_password(args.db):
        print(
            f"note: put the URL in {DB_URL_ENV} to keep the password out of your shell history.",
            file=sys.stderr,
        )
    return url


def _has_password(url: str) -> bool:
    try:
        return bool(make_url(url).password)
    except ArgumentError:
        return False


def build_connector(args: argparse.Namespace) -> tuple[Connector, str]:
    """The connector for the chosen source and a short name for titles and files."""
    url = _database_url(args)
    if url is None:
        if args.table or args.query:
            msg = "--table/--query apply to databases, not to data files"
            raise UsageError(msg)
        path: Path = args.path
        return FileConnector(path, sheet=args.sheet), path.name
    if bool(args.table) == bool(args.query):
        msg = "for a database, give exactly one of --table or --query"
        raise UsageError(msg)
    connector = SQLConnector(
        url, table=args.table, query=args.query, schema=args.schema, limit=args.limit
    )
    return connector, args.table or "query"


def print_summary(analysis: Analysis, out: TextIO) -> None:
    p, roles = analysis.profile, analysis.insights.roles
    out.write(
        f"{analysis.name}: {p.rows:,} rows x {p.n_columns} columns, "
        f"{p.completeness_pct:.1f}% complete, {p.duplicate_rows:,} duplicate rows\n"
    )
    date = f" over {roles.date}" if roles.date else ""
    out.write(f"Analyzing {roles.measure_label}{date}\n")

    out.write(f"\nData quality ({len(p.issues)} issues)\n")
    for issue in p.issues:
        out.write(f"  [{issue.severity:<8}] {issue.column or '(table)'}: {issue.message}\n")

    out.write(f"\nInsights ({len(analysis.insights.insights)})\n")
    for n, insight in enumerate(analysis.insights.insights, 1):
        out.write(f"  {n:>2}. {insight.title}\n      {insight.detail}\n")


def _default_output(args: argparse.Namespace, name: str) -> Path:
    if args.path is not None and args.path.suffix.lower() not in SQLITE_EXTENSIONS:
        result: Path = args.path.with_suffix(".report.html")
        return result
    return Path(f"{name}.report.html")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy codepage
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        if args.command == "tables":
            url = _database_url(args)
            if url is None:
                msg = "`tables` needs a database: a SQLite file or --db URL"
                raise UsageError(msg)
            for name in list_tables(url, schema=args.schema):
                print(name)
            return 0

        connector, name = build_connector(args)
        analysis = analyze(
            connector,
            name,
            measure=args.measure,
            date=args.date_column,
            aggregation=Aggregation(args.aggregation) if args.aggregation else None,
        )
    except UsageError as exc:
        parser.error(str(exc))  # exits with status 2
    except ConnectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "summary":
        print_summary(analysis, sys.stdout)
        return 0

    from insights_forge.report import write_html

    output: Path = args.output or _default_output(args, name)
    write_html(analysis, output, offline=args.offline)
    print(f"Report written to {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
