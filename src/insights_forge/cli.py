"""Command-line interface.

insights-forge summary data/sample/sales.csv
insights-forge report data/sample/sales.csv -o report.html
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from insights_forge import __version__
from insights_forge.connectors import ConnectorError
from insights_forge.pipeline import Analysis, analyze_file


def _add_source_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("path", type=Path, help="CSV, Excel, Parquet or JSON file")
    parser.add_argument("--sheet", help="Excel sheet name (default: first sheet)")
    parser.add_argument("--measure", help="numeric column to analyze (default: auto-detect)")
    parser.add_argument("--date-column", help="date column for trends (default: auto-detect)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="insights-forge",
        description="Automatic data-quality checks and insights for your data files.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    summary = sub.add_parser("summary", help="print data-quality issues and insights")
    _add_source_args(summary)

    report = sub.add_parser("report", help="write a shareable HTML report")
    _add_source_args(report)
    report.add_argument(
        "-o", "--output", type=Path, help="output file (default: <input>.report.html)"
    )
    report.add_argument(
        "--offline",
        action="store_true",
        help="embed plotly.js so the report works without internet (+4.5 MB)",
    )
    return parser


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


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to a legacy codepage
        sys.stdout.reconfigure(encoding="utf-8")

    try:
        analysis = analyze_file(
            args.path, sheet=args.sheet, measure=args.measure, date=args.date_column
        )
    except ConnectorError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.command == "summary":
        print_summary(analysis, sys.stdout)
        return 0

    from insights_forge.report import write_html

    output: Path = args.output or args.path.with_suffix(".report.html")
    write_html(analysis, output, offline=args.offline)
    print(f"Report written to {output}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
