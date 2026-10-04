"""Deterministic mock data with the same schema as `thelook_ecommerce`.

Brands and customers are fictional. Two patterns are planted so "why" questions have an answer:
- customers in Texas buy fewer items and get ~20% lower prices (the "underspending state")
- the brand "Driftline" has a much higher return rate than the others
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import pandas as pd

from retail_agent.data.schema import DATASET, TABLES, split_dataset

END_DATE = datetime(2025, 9, 30)  # fixed default, so tests are reproducible
HISTORY_DAYS = 1000

BRANDS = {
    "Alder & Finch": "Women",
    "Brightwave": "Women",
    "Cobalt Row": "Men",
    "Driftline": "Men",
    "Evergreen Co": "Men",
    "Foxglove": "Women",
    "Granite Peak": "Men",
    "Harbor Lane": "Women",
    "Ironwood": "Men",
    "Juniper Mill": "Women",
    "Kestrel": "Men",
    "Lumen": "Women",
}
CATEGORIES = {
    "Jeans": (45, 120),
    "Tops & Tees": (15, 55),
    "Sweaters": (40, 110),
    "Outerwear & Coats": (90, 260),
    "Accessories": (10, 60),
    "Active": (25, 80),
    "Dresses": (50, 150),
    "Shorts": (20, 60),
    "Swim": (20, 70),
    "Socks": (6, 20),
}
LOCATIONS = [  # (state, city, country, weight)
    ("California", "Los Angeles", "United States", 14),
    ("Texas", "Austin", "United States", 12),
    ("New York", "New York", "United States", 10),
    ("Florida", "Miami", "United States", 8),
    ("Illinois", "Chicago", "United States", 6),
    ("Washington", "Seattle", "United States", 5),
    ("Ontario", "Toronto", "Canada", 4),
    ("England", "London", "United Kingdom", 5),
    ("Bavaria", "Munich", "Germany", 3),
    ("Sao Paulo", "Sao Paulo", "Brasil", 4),
]
TRAFFIC = ["Search", "Organic", "Facebook", "Email", "Display"]
FIRST = [
    "Alex",
    "Sam",
    "Jordan",
    "Taylor",
    "Morgan",
    "Casey",
    "Riley",
    "Jamie",
    "Avery",
    "Quinn",
    "Maria",
    "Chen",
    "Priya",
    "Omar",
    "Lena",
    "Diego",
    "Aiko",
    "Noah",
    "Zoe",
    "Ivan",
]
LAST = [
    "Smith",
    "Garcia",
    "Nguyen",
    "Brown",
    "Khan",
    "Silva",
    "Miller",
    "Kim",
    "Rossi",
    "Weber",
    "Lopez",
    "Patel",
    "Jones",
    "Costa",
    "Novak",
    "Haddad",
    "Larsen",
    "Okafor",
    "Sato",
    "Reyes",
]
STATUSES = ["Complete", "Shipped", "Processing", "Cancelled", "Returned"]
STATUS_WEIGHTS = [55, 15, 10, 10, 10]
# Heavier shopping in Nov/Dec, a little in summer.
MONTH_WEIGHT = {1: 7, 2: 6, 3: 7, 4: 7, 5: 8, 6: 8, 7: 9, 8: 8, 9: 8, 10: 9, 11: 13, 12: 14}

_DUCKDB_TYPES = {
    "INT64": "BIGINT",
    "FLOAT64": "DOUBLE",
    "STRING": "VARCHAR",
    "TIMESTAMP": "TIMESTAMP",
}


def generate(
    seed: int = 42,
    end_date: datetime = END_DATE,
    n_users: int = 600,
    n_products: int = 90,
    n_orders: int = 2500,
) -> dict[str, pd.DataFrame]:
    """Orders cover the `HISTORY_DAYS` up to `end_date`."""
    rng = random.Random(seed)
    start_date = end_date - timedelta(days=HISTORY_DAYS)

    # users
    loc_weights = [w for *_, w in LOCATIONS]
    users = []
    for uid in range(1, n_users + 1):
        state, city, country, _ = rng.choices(LOCATIONS, weights=loc_weights)[0]
        first, last = rng.choice(FIRST), rng.choice(LAST)
        created = (
            start_date
            - timedelta(days=rng.randint(0, 365))
            + timedelta(days=rng.randint(0, (end_date - start_date).days - 30))
        )
        users.append(
            {
                "id": uid,
                "first_name": first,
                "last_name": last,
                "email": f"{first}.{last}.{uid}@example.com".lower(),
                "age": rng.randint(14, 70),
                "gender": rng.choice(["M", "F"]),
                "state": state,
                "street_address": f"{rng.randint(1, 9999)} {rng.choice(LAST)} Street",
                "postal_code": f"{rng.randint(10000, 99999)}",
                "city": city,
                "country": country,
                "latitude": round(rng.uniform(25, 60), 6),
                "longitude": round(rng.uniform(-123, 20), 6),
                "traffic_source": rng.choice(TRAFFIC),
                "created_at": created,
            }
        )
    users_df = pd.DataFrame(users)

    # products
    products = []
    brand_names = list(BRANDS)
    cat_names = list(CATEGORIES)
    for pid in range(1, n_products + 1):
        brand = brand_names[(pid - 1) % len(brand_names)]
        category = rng.choice(cat_names)
        lo, hi = CATEGORIES[category]
        price = round(rng.uniform(lo, hi), 2)
        products.append(
            {
                "id": pid,
                "cost": round(price * rng.uniform(0.4, 0.65), 2),
                "category": category,
                "name": f"{brand} {category} {pid}",
                "brand": brand,
                "retail_price": price,
                "department": BRANDS[brand],
                "sku": f"SKU{pid:06d}",
                "distribution_center_id": rng.randint(1, 10),
            }
        )
    products_df = pd.DataFrame(products)
    product_rows = products_df.to_dict("records")

    # orders and order items; a few customers order much more than the rest
    user_state = dict(zip(users_df["id"], users_df["state"], strict=True))
    user_created = dict(zip(users_df["id"], users_df["created_at"], strict=True))
    user_gender = dict(zip(users_df["id"], users_df["gender"], strict=True))
    user_weights = [8 if rng.random() < 0.05 else 1 for _ in range(n_users)]
    days = (end_date - start_date).days
    day_weights = [MONTH_WEIGHT[(start_date + timedelta(days=d)).month] for d in range(days + 1)]

    orders, items = [], []
    item_id = 1
    for oid in range(1, n_orders + 1):
        uid = rng.choices(range(1, n_users + 1), weights=user_weights)[0]
        offset = rng.choices(range(days + 1), weights=day_weights)[0]
        created = max(
            start_date + timedelta(days=offset, seconds=rng.randint(0, 86399)),
            user_created[uid] + timedelta(days=1),
        )
        if created > end_date:
            created = end_date - timedelta(seconds=rng.randint(0, 86399))
        in_texas = user_state[uid] == "Texas"
        n_items = rng.choice([1, 1, 2]) if in_texas else rng.choice([1, 1, 2, 2, 3, 4])
        status = rng.choices(STATUSES, weights=STATUS_WEIGHTS)[0]
        shipped = delivered = returned = None
        if status in ("Shipped", "Complete", "Returned"):
            shipped = created + timedelta(days=rng.randint(1, 3))
        if status in ("Complete", "Returned"):
            delivered = shipped + timedelta(days=rng.randint(2, 5))
        if status == "Returned":
            returned = delivered + timedelta(days=rng.randint(1, 10))
        orders.append(
            {
                "order_id": oid,
                "user_id": uid,
                "status": status,
                "gender": user_gender[uid],
                "created_at": created,
                "returned_at": returned,
                "shipped_at": shipped,
                "delivered_at": delivered,
                "num_of_item": n_items,
            }
        )
        for _ in range(n_items):
            p = rng.choice(product_rows)
            price = p["retail_price"] * (0.78 if in_texas else rng.uniform(0.9, 1.0))
            item_status, item_returned = status, returned
            if p["brand"] == "Driftline" and status == "Complete" and rng.random() < 0.35:
                item_status = "Returned"
                item_returned = delivered + timedelta(days=rng.randint(1, 10))
            items.append(
                {
                    "id": item_id,
                    "order_id": oid,
                    "user_id": uid,
                    "product_id": p["id"],
                    "inventory_item_id": item_id * 3 + 7,
                    "status": item_status,
                    "created_at": created,
                    "shipped_at": shipped,
                    "delivered_at": delivered,
                    "returned_at": item_returned,
                    "sale_price": round(price, 2),
                }
            )
            item_id += 1

    return {
        "users": users_df,
        "products": products_df,
        "orders": pd.DataFrame(orders),
        "order_items": pd.DataFrame(items),
    }


def write_duckdb(
    frames: dict[str, pd.DataFrame], con: duckdb.DuckDBPyConnection, namespace: str
) -> None:
    """Create the tables in `namespace` from the canonical schema, so the types always match."""
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {namespace}")
    for table, columns in TABLES.items():
        ddl = ", ".join(f"{c.name} {_DUCKDB_TYPES[c.type]}" for c in columns)
        con.execute(f"CREATE OR REPLACE TABLE {namespace}.{table} ({ddl})")
        df = frames[table][[c.name for c in columns]]  # noqa: F841 (read by DuckDB by name)
        con.execute(f"INSERT INTO {namespace}.{table} SELECT * FROM df")


def build_mock_db(
    path: str | Path, dataset: str = DATASET, seed: int = 42, end_date: datetime | None = None
) -> Path:
    """Write the mock database file. Tables live in a schema named after the BigQuery dataset.

    Orders run up to today by default, like the real dataset, so "last month" means the same thing
    locally and in BigQuery.
    """
    end_date = end_date or datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    try:
        write_duckdb(generate(seed=seed, end_date=end_date), con, split_dataset(dataset)[1])
    finally:
        con.close()
    return path


if __name__ == "__main__":
    from retail_agent.config import Settings

    settings = Settings.from_env()
    out = build_mock_db(settings.duckdb_path, settings.bq_dataset)
    print(f"Mock database written to {out}")
