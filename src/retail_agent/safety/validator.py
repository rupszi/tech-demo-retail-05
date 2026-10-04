"""SQL gate: every query the model writes passes through `validate_query` before it runs.

The query is parsed, checked against allow-lists, rewritten for the user's scope and regenerated
from the syntax tree. Only the regenerated SQL is ever executed, so nothing hidden in comments or
odd formatting survives. Rules are default-deny: anything not recognised as safe is rejected.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import traverse_scope

from retail_agent.data.schema import DATASET, split_dataset
from retail_agent.safety.policy import (
    ALLOWED_FUNCTION_NAMESPACES,
    ALLOWED_TABLES,
    FORBIDDEN_FUNCTIONS,
    PII_COLUMNS,
)
from retail_agent.safety.profiles import UserProfile
from retail_agent.safety.scoping import scope_tables

_WRITE_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Command", "Set", "Use", "Grant", "Copy", "Into",
    )
    if hasattr(exp, name)
)  # fmt: skip
_FROM_SOURCES = (exp.Table, exp.Subquery, exp.Unnest)
_TABLE_CLAUSES = frozenset({"this", "db", "catalog", "alias", "joins", "pivots"})
_ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The parser warns on stderr when it meets syntax it does not know; those queries are rejected.
logging.getLogger("sqlglot").setLevel(logging.ERROR)


class SqlRejected(Exception):
    """The query broke a rule. `retryable` says whether the model may try to rewrite it."""

    def __init__(self, code: str, message: str, retryable: bool):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str  # safe to execute
    tables: tuple[str, ...]
    limit: int


def validate_query(
    sql: str, profile: UserProfile, *, max_rows: int, dataset: str = DATASET
) -> ValidatedQuery:
    tree = _parse_single_select(sql)
    _reject_forbidden_nodes(tree)
    tables = _real_tables(tree, dataset)
    _reject_pii_columns(tree)
    table_names = tuple(sorted({t.name.lower() for t in tables}))
    scope_tables(tables, profile, dataset)
    tree, limit = _enforce_limit(tree, max_rows)
    return ValidatedQuery(tree.sql(dialect="bigquery", comments=False), table_names, limit)


def _parse_single_select(sql: str) -> exp.Expression:
    try:
        statements = [s for s in sqlglot.parse(sql, read="bigquery") if s is not None]
    except SqlglotError as e:
        message = _ANSI.sub("", str(e))
        raise SqlRejected("syntax", f"The SQL could not be parsed: {message}", True) from e
    if not statements:
        raise SqlRejected("empty", "No SQL statement was provided.", True)
    if len(statements) > 1:
        raise SqlRejected("multiple_statements", "Only a single statement is allowed.", False)
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SqlRejected("not_select", "Only SELECT queries are allowed.", False)
    return tree


def _reject_forbidden_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, _WRITE_NODES):
            raise SqlRejected("not_select", "Only SELECT queries are allowed.", False)
        if isinstance(node, exp.From | exp.Join) and not isinstance(node.this, _FROM_SOURCES):
            raise SqlRejected(
                "table_function", "FROM may only use tables, subqueries and UNNEST.", False
            )
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            namespace = node.this.name.upper() if isinstance(node.this, exp.Identifier) else ""
            if namespace not in ALLOWED_FUNCTION_NAMESPACES:
                raise _forbidden_function(node.sql(dialect="bigquery").split("(")[0])
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if "." in name or name in FORBIDDEN_FUNCTIONS:
                raise _forbidden_function(name)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return node.name.upper()
    try:
        return node.sql_name().upper()
    except NotImplementedError:
        return type(node).__name__.upper()


def _forbidden_function(name: str) -> SqlRejected:
    return SqlRejected("forbidden_function", f"Function {name} is not allowed.", False)


def _real_tables(tree: exp.Expression, dataset: str) -> list[exp.Table]:
    """Return every reference to a real table, after checking it is on the allow-list.

    A table reference is only treated as a WITH name when scope analysis proves that name is
    visible at that point. Everything else is a real table and must be allowed.
    """
    try:
        scopes = traverse_scope(tree)
    except SqlglotError as e:
        raise SqlRejected("syntax", f"The SQL could not be analysed: {e}", True) from e
    with_refs: set[int] = set()
    for scope in scopes:
        visible = {name.lower() for name in scope.cte_sources}
        with_refs.update(id(t) for t in scope.tables if not t.db and t.name.lower() in visible)

    shadowed = {cte.alias.lower() for cte in tree.find_all(exp.CTE)} & ALLOWED_TABLES
    if shadowed:
        raise SqlRejected(
            "with_shadows_table",
            f"A WITH clause may not reuse a table name ({', '.join(sorted(shadowed))}).",
            True,
        )

    project, dataset_name = split_dataset(dataset)
    real_tables: list[exp.Table] = []
    for table in tree.find_all(exp.Table):
        if id(table) in with_refs:
            continue
        if not isinstance(table.this, exp.Identifier):
            raise SqlRejected("table_function", "Table functions are not allowed.", False)
        name, db, catalog = table.name.lower(), table.db.lower(), table.catalog.lower()
        qualifier_ok = (not db and not catalog) or (
            db == dataset_name.lower() and catalog in ("", project.lower())
        )
        if name not in ALLOWED_TABLES or not qualifier_ok:
            shown = ".".join(part for part in (table.catalog, table.db, table.name) if part)
            raise SqlRejected(
                "unknown_table",
                f"Table {shown} is not available. Use only: {', '.join(sorted(ALLOWED_TABLES))}.",
                True,
            )
        unsupported = sorted(k for k, v in table.args.items() if v and k not in _TABLE_CLAUSES)
        if unsupported:
            raise SqlRejected(
                "unsupported_table_clause",
                f"Table clause not supported on {name}: {', '.join(unsupported)}.",
                True,
            )
        real_tables.append(table)
    return real_tables


def _reject_pii_columns(tree: exp.Expression) -> None:
    """Early, friendly rejection. The hard guarantee is that scoping never selects these columns."""
    used = sorted({c.name.lower() for c in tree.find_all(exp.Column)} & PII_COLUMNS)
    if used:
        raise SqlRejected(
            "pii_column",
            f"Personal data columns cannot be used: {', '.join(used)}. "
            "Identify customers by id. Age, gender, city, state and country may be used.",
            True,
        )


def _enforce_limit(tree: exp.Expression, max_rows: int) -> tuple[exp.Expression, int]:
    limit = tree.args.get("limit")
    value = limit.expression if limit is not None else None
    if isinstance(value, exp.Literal) and value.is_int and int(value.name) <= max_rows:
        return tree, int(value.name)
    return tree.limit(max_rows), max_rows
