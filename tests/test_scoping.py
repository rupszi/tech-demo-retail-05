"""Row-level isolation, checked against ground truth computed independently with pandas."""

import pytest

from retail_agent.safety import UserProfile
from retail_agent.safety.policy import PII_COLUMNS, SAFE_USER_COLUMNS

RESTRICTED = ["alice", "bob", "dan"]


def in_scope_products(frames, profile):
    products = frames["products"]
    mask = products["id"].notna()
    if profile.brands is not None:
        mask &= products["brand"].isin(profile.brands)
    if profile.departments is not None:
        mask &= products["department"].isin(profile.departments)
    return set(products.loc[mask, "id"])


def ids(gateway, user, sql):
    return set(gateway(user).run(sql).frame.iloc[:, 0])


@pytest.mark.parametrize("user", RESTRICTED)
def test_each_table_shows_exactly_the_rows_in_scope(user, frames, profiles, gateway):
    allowed = in_scope_products(frames, profiles[user])
    items = frames["order_items"]
    scoped_items = items[items["product_id"].isin(allowed)]

    assert ids(gateway, user, "SELECT id FROM products") == allowed
    assert ids(gateway, user, "SELECT id FROM order_items") == set(scoped_items["id"])
    assert ids(gateway, user, "SELECT order_id FROM orders") == set(scoped_items["order_id"])
    assert ids(gateway, user, "SELECT id FROM users") == set(scoped_items["user_id"])


def test_unrestricted_profile_sees_every_row(frames, gateway):
    for table, key in (("products", "id"), ("order_items", "id"), ("orders", "order_id"),
                       ("users", "id")):  # fmt: skip
        assert ids(gateway, "carol", f"SELECT {key} FROM {table}") == set(frames[table][key])


def test_restricted_scopes_really_restrict(frames, profiles):
    """Guards the test above against a vacuous pass."""
    everything = set(frames["products"]["id"])
    alice, bob = (in_scope_products(frames, profiles[u]) for u in ("alice", "bob"))
    assert alice and bob and alice.isdisjoint(bob)
    assert alice < everything and bob < everything
    assert in_scope_products(frames, profiles["carol"]) == everything


@pytest.mark.parametrize("user", RESTRICTED)
def test_revenue_matches_ground_truth(user, frames, profiles, gateway):
    allowed = in_scope_products(frames, profiles[user])
    items = frames["order_items"]
    expected = items[items["product_id"].isin(allowed)]["sale_price"].sum()
    got = gateway(user).run("SELECT SUM(sale_price) AS revenue FROM order_items").frame
    assert got["revenue"][0] == pytest.approx(expected)


# Queries written to reach products outside the user's scope. Each returns product ids.
ESCAPE_ATTEMPTS = [
    "SELECT id FROM products WHERE brand = 'Driftline' OR brand = 'Brightwave' OR TRUE",
    "SELECT id FROM products WHERE brand NOT IN ('Alder & Finch')",
    "SELECT product_id FROM order_items",
    "SELECT product_id FROM order_items WHERE product_id IN (SELECT id FROM products)",
    "SELECT p.id FROM products p CROSS JOIN products q",
    "SELECT p.id FROM products p RIGHT JOIN order_items oi ON oi.product_id = p.id",
    "SELECT oi.product_id FROM orders o JOIN order_items oi ON oi.order_id = o.order_id",
    "SELECT oi.product_id FROM users u JOIN order_items oi ON oi.user_id = u.id",
    "SELECT id FROM products UNION ALL SELECT product_id FROM order_items",
    "WITH everything AS (SELECT * FROM products) SELECT id FROM everything",
    "SELECT id FROM (SELECT * FROM (SELECT * FROM products))",
    "SELECT id FROM `bigquery-public-data.thelook_ecommerce.products`",
    "SELECT id FROM `bigquery-public-data`.thelook_ecommerce.products",
    "SELECT id FROM thelook_ecommerce.products",
    "SELECT id FROM Products",
    "SELECT (SELECT MAX(id) FROM products) AS id",
    "SELECT id FROM products WHERE EXISTS (SELECT 1 FROM products x WHERE x.id <> products.id)",
    "SELECT oi.product_id FROM order_items oi WHERE oi.order_id IN (SELECT order_id FROM orders)",
]


@pytest.mark.parametrize("user", RESTRICTED)
@pytest.mark.parametrize("sql", ESCAPE_ATTEMPTS)
def test_no_query_returns_products_outside_the_scope(sql, user, frames, profiles, gateway):
    allowed = in_scope_products(frames, profiles[user])
    returned = {v for v in gateway(user).run(sql).frame.iloc[:, 0] if v is not None}
    assert returned <= allowed


def test_another_users_brand_returns_nothing(gateway):
    frame = gateway("alice").run("SELECT * FROM products WHERE brand = 'Driftline'").frame
    assert frame.empty
    frame = gateway("bob").run("SELECT DISTINCT brand FROM products").frame
    assert set(frame["brand"]) == {"Cobalt Row", "Driftline", "Granite Peak"}


def test_department_scope(gateway):
    frame = gateway("dan").run("SELECT DISTINCT department FROM products").frame
    assert set(frame["department"]) == {"Men"}


def test_empty_allow_list_sees_nothing(gateway):
    nobody = UserProfile("nobody", "No access", brands=())
    for table in ("products", "order_items", "orders", "users"):
        assert gateway(nobody).run(f"SELECT COUNT(*) AS n FROM {table}").frame["n"][0] == 0


def test_brand_values_cannot_inject_sql(gateway):
    hostile = UserProfile("x", "x", brands=("x' OR '1'='1", "Driftline') OR ('a'='a"))
    assert gateway(hostile).run("SELECT COUNT(*) AS n FROM products").frame["n"][0] == 0


# ---- personal data --------------------------------------------------------------------------
@pytest.mark.parametrize("user", RESTRICTED + ["carol"])
def test_users_only_ever_exposes_safe_columns(user, gateway):
    frame = gateway(user).run("SELECT * FROM users").frame
    assert tuple(frame.columns) == SAFE_USER_COLUMNS
    joined = gateway(user).run("SELECT * FROM users u JOIN orders o ON o.user_id = u.id").frame
    assert not set(joined.columns) & PII_COLUMNS


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM users",
        "SELECT u FROM users u",
        "SELECT TO_JSON_STRING(u) AS j FROM users u",
        "SELECT * FROM users u JOIN orders o ON o.user_id = u.id",
    ],
)
def test_no_personal_value_reaches_a_result(sql, frames, gateway):
    """Whole-row tricks cannot surface values from the PII columns."""
    text = gateway("carol").run(sql).frame.to_string()
    users = frames["users"]
    assert "@example.com" not in text
    assert not any(address in text for address in users["street_address"].head(50))
    assert not any(str(lat) in text for lat in users["latitude"].head(50))
