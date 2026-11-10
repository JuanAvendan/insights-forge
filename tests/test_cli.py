from __future__ import annotations

from pathlib import Path

import pytest

from insights_forge import __version__
from insights_forge.cli import main

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_summary(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", str(SAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "sales.csv: 1,012 rows x 11 columns" in out
    assert "Analyzing total over order_date" in out
    assert "Data quality (7 issues)" in out
    assert "Insights (" in out


def test_summary_with_role_overrides(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", str(SAMPLE), "--measure", "unit_price"]) == 0
    assert "Analyzing average unit_price" in capsys.readouterr().out


def test_report_default_output_path(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "sales.csv"
    src.write_bytes(SAMPLE.read_bytes())
    assert main(["report", str(src)]) == 0
    out = tmp_path / "sales.report.html"
    assert out.exists()
    assert "Report written to" in capsys.readouterr().out


def test_report_explicit_output(tmp_path: Path) -> None:
    out = tmp_path / "custom.html"
    assert main(["report", str(SAMPLE), "-o", str(out)]) == 0
    assert "Key insights" in out.read_text(encoding="utf-8")


def test_missing_file_is_a_clean_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", str(tmp_path / "nope.csv")]) == 1
    assert "error: File not found" in capsys.readouterr().err


def test_command_is_required() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


# --------------------------------------------------------------------------- databases


def test_tables_of_a_sqlite_file(shop_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["tables", str(shop_db)]) == 0
    assert capsys.readouterr().out.split() == ["revenue_by_region", "sales"]


def test_summary_of_a_sqlite_table(shop_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", str(shop_db), "--table", "sales"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("sales: 1,012 rows")
    assert "Analyzing total over order_date" in out


def test_db_url_from_environment(
    shop_db: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("INSIGHTS_FORGE_DB_URL", f"sqlite:///{shop_db.as_posix()}")
    assert main(["summary", "--query", "SELECT region, total FROM sales", "--limit", "50"]) == 0
    assert capsys.readouterr().out.startswith("query: 50 rows x 2 columns")


def test_password_on_command_line_gets_a_tip(capsys: pytest.CaptureFixture[str]) -> None:
    url = "nosuchdb://bob:s3cret@localhost/shop"  # fails before any network access
    assert main(["tables", "--db", url]) == 1
    err = capsys.readouterr().err
    assert "INSIGHTS_FORGE_DB_URL" in err
    assert "s3cret" not in err


def test_db_report_default_name(
    shop_db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["report", str(shop_db), "--table", "sales"]) == 0
    assert (tmp_path / "sales.report.html").exists()


def test_blocked_query_is_an_error(shop_db: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["summary", str(shop_db), "--query", "DELETE FROM sales"]) == 1
    assert "Only read queries" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["summary"], "give a data file, --db URL"),
        (["summary", "x.csv", "--table", "t"], "apply to databases"),
        (["summary", "x.csv", "--db", "sqlite:///a.db"], "either a file or --db"),
        (["summary", "missing.db", "--table", "t"], "SQLite file not found"),
        (["summary", "--db", "sqlite:///a.db"], "exactly one of --table or --query"),
        (["tables", "x.csv"], "needs a database"),
    ],
)
def test_usage_errors(
    args: list[str],
    message: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("INSIGHTS_FORGE_DB_URL", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    assert message in capsys.readouterr().err
