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
