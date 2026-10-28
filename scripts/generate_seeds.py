"""Generate the fake shop data that the sample warehouse is built on.

Everything is driven by one seed so the CSVs are byte-identical between runs.
That matters more than it looks: if the seeds drifted on every run, every
`git diff` of warehouse/seeds/ would be noise and nobody would review it.

    python scripts/generate_seeds.py            # writes warehouse/seeds/*.csv
    python scripts/generate_seeds.py --scale 0.1  # small set for quick local runs
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

SEED = 42
OUT_DIR = Path(__file__).resolve().parent.parent / "warehouse" / "seeds"

CATEGORIES = {
    "kitchen": (8, 140),
    "outdoor": (15, 400),
    "books": (6, 45),
    "electronics": (25, 900),
    "apparel": (10, 120),
    "toys": (5, 80),
}
# Raw status values are deliberately messy; stg_orders has to clean them up.
STATUSES = [
    ("completed", 55),
    ("Completed", 6),
    ("COMPLETE", 3),
    ("shipped", 14),
    (" shipped ", 2),
    ("placed", 8),
    ("returned", 5),
    ("cancelled", 5),
    ("canceled", 2),
]
DISCOUNTS = [(0.0, 70), (0.05, 15), (0.10, 10), (0.20, 5)]
METHODS = [("card", 70), ("paypal", 15), ("gift_card", 8), ("bank_transfer", 7)]


def weighted(rng: random.Random, options):
    values, weights = zip(*options, strict=True)
    return rng.choices(values, weights=weights, k=1)[0]


def write_csv(name: str, header: list[str], rows: list[tuple]) -> None:
    path = OUT_DIR / f"{name}.csv"
    # newline="" or Windows writes \r\r\n and the CSVs differ per OS
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {name:<12} {len(rows):>7,} rows")


def main(scale: float) -> None:
    Faker.seed(SEED)
    fake = Faker("en_US")
    rng = random.Random(SEED)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    n_customers = int(5_000 * scale)
    n_products = int(500 * scale) or 1
    n_orders = int(50_000 * scale)
    start, end = date(2024, 1, 1), date(2026, 9, 30)

    customers = []
    for cid in range(1, n_customers + 1):
        first, last = fake.first_name(), fake.last_name()
        # Mixed-case emails on purpose, so stg_customers has a reason to lowercase them
        email = f"{first}.{last}{cid}@{fake.free_email_domain()}"
        if rng.random() < 0.7:
            email = email.lower()
        signup = fake.date_between(start_date=start, end_date=end - timedelta(days=30))
        customers.append((cid, first, last, email, fake.city(), signup.isoformat()))
    write_csv("customers", ["id", "first_name", "last_name", "email", "city", "signup_date"], customers)

    products, prices = [], {}
    for pid in range(1, n_products + 1):
        category = rng.choice(list(CATEGORIES))
        lo, hi = CATEGORIES[category]
        price = round(rng.uniform(lo, hi), 2)
        prices[pid] = price
        name = f"{fake.color_name()} {fake.word().title()} {category.rstrip('s').title()}"
        products.append((pid, name, category, f"{price:.2f}"))
    write_csv("products", ["id", "name", "category", "unit_price"], products)

    signup_by_customer = {c[0]: date.fromisoformat(c[5]) for c in customers}
    orders, items, payments = [], [], []
    for oid in range(1, n_orders + 1):
        cid = rng.randint(1, n_customers)
        # Orders can't predate the customer's signup; real data has this bug, ours doesn't
        first_day = signup_by_customer[cid]
        order_day = first_day + timedelta(days=rng.randint(0, (end - first_day).days))
        status = weighted(rng, STATUSES)
        discount = weighted(rng, DISCOUNTS)
        orders.append((oid, cid, order_day.isoformat(), status, f"{discount:.2f}"))

        gross = 0.0
        # 1-4 lines per order, averages out to ~2.4 lines, so ~120k item rows at full scale
        for _ in range(weighted(rng, [(1, 30), (2, 25), (3, 25), (4, 20)])):
            pid = rng.randint(1, n_products)
            qty = weighted(rng, [(1, 70), (2, 20), (3, 7), (5, 3)])
            items.append((len(items) + 1, oid, pid, qty))
            gross += qty * prices[pid]

        if status.strip().lower() in ("cancelled", "canceled", "placed"):
            continue  # nothing collected yet
        # Amounts are stored in cents, the way most payment providers send them
        net_cents = round(gross * (1 - discount) * 100)
        if rng.random() < 0.05 and net_cents > 1000:
            gift = rng.randint(500, net_cents // 2)
            payments.append((len(payments) + 1, oid, "gift_card", gift))
            payments.append((len(payments) + 1, oid, weighted(rng, METHODS), net_cents - gift))
        else:
            payments.append((len(payments) + 1, oid, weighted(rng, METHODS), net_cents))

    write_csv("orders", ["id", "customer_id", "order_date", "status", "discount"], orders)
    write_csv("order_items", ["id", "order_id", "product_id", "quantity"], items)
    write_csv("payments", ["id", "order_id", "method", "amount"], payments)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scale", type=float, default=1.0, help="row-count multiplier (default 1.0)")
    args = parser.parse_args()
    print(f"Writing seeds to {OUT_DIR}")
    main(args.scale)
