from retail_agent.data.mock import generate
from retail_agent.data.schema import TABLES


def test_deterministic():
    a, b = generate(seed=7), generate(seed=7)
    for table in TABLES:
        assert a[table].equals(b[table])
    assert not generate(seed=8)["users"].equals(a["users"])


def test_columns_match_schema(frames):
    for table, cols in TABLES.items():
        assert list(frames[table].columns) == [c.name for c in cols]


def test_referential_integrity(frames):
    user_ids = set(frames["users"]["id"])
    product_ids = set(frames["products"]["id"])
    order_ids = set(frames["orders"]["order_id"])
    assert set(frames["orders"]["user_id"]) <= user_ids
    assert set(frames["order_items"]["user_id"]) <= user_ids
    assert set(frames["order_items"]["product_id"]) <= product_ids
    assert set(frames["order_items"]["order_id"]) <= order_ids


def test_num_of_item_matches_items(frames):
    counts = frames["order_items"].groupby("order_id").size()
    orders = frames["orders"].set_index("order_id")["num_of_item"]
    assert (counts == orders.loc[counts.index]).all()


def test_planted_patterns(frames):
    items, users, products = frames["order_items"], frames["users"], frames["products"]
    df = items.merge(users[["id", "state"]], left_on="user_id", right_on="id", suffixes=("", "_u"))
    avg = df.groupby("state")["sale_price"].mean()
    assert avg["Texas"] < avg.drop("Texas").mean()
    df = items.merge(
        products[["id", "brand"]], left_on="product_id", right_on="id", suffixes=("", "_p")
    )
    ret = df.assign(r=df["status"] == "Returned").groupby("brand")["r"].mean()
    assert ret["Driftline"] > 2 * ret.drop("Driftline").mean()
