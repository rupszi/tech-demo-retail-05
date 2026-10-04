"""Row- and column-level scoping, applied to the parsed query rather than to the prompt.

Every reference to a real table is replaced by a subquery that only exposes what the user may see:

- `products`, `order_items`: only rows for products of the user's brands
- `orders`: only orders that contain at least one in-scope item
- `users`: only customers who bought an in-scope product, and never the PII columns

Because the replacement happens on the syntax tree, the model cannot write its way around it. The
subqueries use fully qualified table names, so a WITH clause in the query cannot stand in for them.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from retail_agent.data.schema import TABLES, split_dataset
from retail_agent.safety.policy import SAFE_USER_COLUMNS
from retail_agent.safety.profiles import UserProfile


def _qualified(table: str, dataset: str) -> exp.Table:
    """`project.dataset.table`: the only form in which a real table is ever queried."""
    project, name = split_dataset(dataset)
    return exp.Table(
        this=exp.to_identifier(table),
        db=exp.to_identifier(name),
        catalog=exp.to_identifier(project),
    )


def _product_filter(profile: UserProfile) -> exp.Expression | None:
    """The condition on `products` for this user. None means every brand is allowed."""
    if profile.all_brands:
        return None
    if not profile.brands:
        return exp.false()  # no brand scope means no access
    # Brand names become string literals in the tree, so the generator escapes them (Levi's).
    return exp.column("brand").isin(*[exp.Literal.string(b) for b in profile.brands])


def _columns(table: str) -> list[str]:
    """The columns a scoped table exposes. For `users` the personal data columns are left out."""
    if table == "users":
        return list(SAFE_USER_COLUMNS)
    return [c.name for c in TABLES[table]]


def _scoped_select(table: str, profile: UserProfile, dataset: str) -> exp.Select:
    """What this user may see of `table`, as a SELECT over the real table."""
    # Columns are listed by name, never `*`: this is what keeps personal data out of `users`
    # whatever the outer query selects.
    select = sqlglot.select(*_columns(table)).from_(_qualified(table, dataset))
    product_filter = _product_filter(profile)
    if product_filter is None:
        return select
    if table == "products":
        return select.where(product_filter)

    in_scope_products = (
        sqlglot.select("id").from_(_qualified("products", dataset)).where(product_filter)
    )
    item_in_scope = exp.column("product_id").isin(query=in_scope_products)
    if table == "order_items":
        return select.where(item_in_scope)

    # orders and users are reached through the in-scope order items
    key, item_key = ("order_id", "order_id") if table == "orders" else ("id", "user_id")
    in_scope_keys = (
        sqlglot.select(item_key).from_(_qualified("order_items", dataset)).where(item_in_scope)
    )
    return select.where(exp.column(key).isin(query=in_scope_keys))


def scope_tables(tables: list[exp.Table], profile: UserProfile, dataset: str) -> None:
    """Rewrite each table reference in place. Call with the list collected before any rewrite."""
    for table in tables:
        name = table.name.lower()
        if profile.all_brands and name != "users":
            # Nothing to filter: just pin the reference to the fully qualified table.
            qualified = _qualified(name, dataset)
            table.set("this", qualified.this)
            table.set("db", qualified.args["db"])
            table.set("catalog", qualified.args["catalog"])
            continue
        table.replace(
            exp.Subquery(
                this=_scoped_select(name, profile, dataset),
                # Keep the name the query uses for the table, so its column references resolve.
                alias=exp.TableAlias(this=exp.to_identifier(table.alias or name)),
                # The parser hangs joins and pivots on the table node; they move with it.
                joins=table.args.get("joins"),
                pivots=table.args.get("pivots"),
            )
        )
