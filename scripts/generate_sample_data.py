"""Generate the synthetic sample dataset at data/sample/sales.csv.

The data is fake but realistic, and it intentionally contains common
data-quality problems (missing values, duplicates, outliers, invalid values)
so the profiler has something to find.

Run with:  uv run python scripts/generate_sample_data.py
"""

from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

SEED = 42
N_ROWS = 1_000
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "sample" / "sales.csv"

REGIONS = {"North": 1.0, "South": 0.8, "East": 1.2, "West": 0.9}
PRODUCTS = {
    "Laptop": ("Electronics", 950.0),
    "Monitor": ("Electronics", 220.0),
    "Headphones": ("Electronics", 85.0),
    "Desk": ("Furniture", 310.0),
    "Office Chair": ("Furniture", 180.0),
    "Notebook": ("Stationery", 4.5),
    "Pen Pack": ("Stationery", 6.0),
}
CHANNELS = ["Online", "Store", "Phone"]
FIELDS = [
    "order_id", "order_date", "region", "channel", "product", "category",
    "unit_price", "quantity", "discount", "total", "customer_email",
]  # fmt: skip


def make_row(rng: random.Random, order_id: int, start: date) -> dict[str, object]:
    product = rng.choice(list(PRODUCTS))
    category, base_price = PRODUCTS[product]
    region = rng.choices(list(REGIONS), weights=list(REGIONS.values()))[0]
    day = rng.randint(0, 364)
    # Mild upward trend over the year plus a Q4 bump.
    trend = 1 + day / 365 * 0.15 + (0.2 if day >= 273 else 0)
    unit_price = round(base_price * rng.uniform(0.9, 1.1), 2)
    quantity = max(1, round(rng.expovariate(1 / 3) * trend))
    discount = rng.choice([0, 0, 0, 0.05, 0.1, 0.15])
    return {
        "order_id": f"ORD-{order_id:05d}",
        "order_date": (start + timedelta(days=day)).isoformat(),
        "region": region,
        "channel": rng.choice(CHANNELS),
        "product": product,
        "category": category,
        "unit_price": unit_price,
        "quantity": quantity,
        "discount": discount,
        "total": round(unit_price * quantity * (1 - discount), 2),
        "customer_email": f"customer{rng.randint(1, 400)}@example.com",
    }


def inject_quality_issues(rng: random.Random, rows: list[dict[str, object]]) -> None:
    for row in rng.sample(rows, k=len(rows) // 20):  # ~5% missing emails
        row["customer_email"] = None
    for row in rng.sample(rows, k=len(rows) // 50):  # ~2% missing region
        row["region"] = None
    for row in rng.sample(rows, k=5):  # quantity outliers (valid but extreme orders)
        quantity = rng.randint(200, 500)
        price, discount = float(str(row["unit_price"])), float(str(row["discount"]))
        row["quantity"] = quantity
        row["total"] = round(price * quantity * (1 - discount), 2)
    for row in rng.sample(rows, k=3):  # invalid negative prices
        row["unit_price"] = -abs(float(str(row["unit_price"])))
    rows.extend(dict(row) for row in rng.sample(rows, k=12))  # exact duplicates


def main() -> None:
    rng = random.Random(SEED)  # noqa: S311 - not used for security
    start = date(2025, 1, 1)
    rows = [make_row(rng, i, start) for i in range(1, N_ROWS + 1)]
    inject_quality_issues(rng, rows)
    rng.shuffle(rows)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
