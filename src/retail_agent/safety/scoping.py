"""Row- and column-level scoping, applied to the parsed query rather than to the prompt.

Every reference to a real table is replaced by a subquery that only exposes what the user may see:

- `products`, `order_items`: only rows for products in the user's scope
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
    project, name = split_dataset(dataset)
    return exp.Table(
        this=exp.to_identifier(table),
        db=exp.to_identifier(name),
        catalog=exp.to_identifier(project),
    )


def _product_filter(profile: UserProfile) -> exp.Expression | None:
    conditions: list[exp.Expression] = []
    for column, values in (("brand", profile.brands), ("department", profile.departments)):
        if values is None:
            continue
        if not values:  # an empty allow-list means "nothing"
            conditions.append(exp.false())
        else:
            conditions.append(exp.column(column).isin(*[exp.Literal.string(v) for v in values]))
    return exp.and_(*conditions) if conditions else None


def _columns(table: str) -> list[str]:
    if table == "users":
        return list(SAFE_USER_COLUMNS)
    return [c.name for c in TABLES[table]]


def _scoped_select(table: str, profile: UserProfile, dataset: str) -> exp.Select:
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
        if profile.is_unrestricted and name != "users":
            # Nothing to filter: just pin the reference to the fully qualified table.
            qualified = _qualified(name, dataset)
            table.set("this", qualified.this)
            table.set("db", qualified.args["db"])
            table.set("catalog", qualified.args["catalog"])
            continue
        table.replace(
            exp.Subquery(
                this=_scoped_select(name, profile, dataset),
                alias=exp.TableAlias(this=exp.to_identifier(table.alias or name)),
                joins=table.args.get("joins"),
                pivots=table.args.get("pivots"),
            )
        )
