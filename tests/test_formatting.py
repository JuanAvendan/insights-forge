from __future__ import annotations

import pytest

from insights_forge.formatting import fmt_number


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1234567.891, "1,234,568"),
        (244581.78, "244,582"),
        (286.93, "286.9"),
        (12.345, "12.3"),
        (0.0508, "0.0508"),
        (3.0, "3"),
        (-983.9, "-983.9"),
        (0, "0"),
        (None, "-"),
        (float("nan"), "-"),
        (float("inf"), "∞"),
        (float("-inf"), "-∞"),
    ],
)
def test_fmt_number(value: float | None, expected: str) -> None:
    assert fmt_number(value) == expected
