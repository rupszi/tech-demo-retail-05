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

# Any of these anywhere in the tree means the statement is not a pure read. Looked up by name
# because not every parser version has every node.
_WRITE_NODES = tuple(
    getattr(exp, name)
    for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Command", "Set", "Use", "Grant", "Copy", "Into",
    )
    if hasattr(exp, name)
)  # fmt: skip
_PARAMETERS = tuple(
    getattr(exp, name)
    for name in ("Parameter", "SessionParameter", "Placeholder")
    if hasattr(exp, name)
)
# What FROM and JOIN may read from. A table function (EXTERNAL_QUERY, ML.PREDICT) is none of these.
_FROM_SOURCES = (exp.Table, exp.Subquery, exp.Unnest)
# The parts of a table reference that the scope rewrite carries over. Anything else on a table
# (time travel, sampling, a hint) is rejected rather than silently dropped by the rewrite.
_TABLE_CLAUSES = frozenset({"this", "db", "catalog", "alias", "joins", "pivots"})
_ANSI = re.compile(r"\x1b\[[0-9;]*m")  # the parser colours its errors; the model gets plain text

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
    # The order matters: every check runs on the query as the model wrote it, and the rewrite
    # comes last, so the checks never see (or trip over) the subqueries the rewrite adds.
    tree = _parse_single_select(sql)
    _reject_forbidden_nodes(tree)
    tables = _real_tables(tree, dataset)
    _reject_pii_columns(tree)
    table_names = tuple(sorted({t.name.lower() for t in tables}))  # read before they are replaced
    scope_tables(tables, profile, dataset)
    tree, limit = _enforce_limit(tree, max_rows)
    # What runs is generated from the checked tree, without comments: never the model's own text.
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
        # Several queries in one call is an honest way to save a step: the model is told to
        # send them one at a time and may try again. If anything other than a query is among
        # them (SELECT 1; DROP TABLE ...), it is not an honest mistake and the attempts end.
        only_queries = all(isinstance(s, exp.Query) for s in statements)
        raise SqlRejected(
            "multiple_statements",
            "Only a single statement is allowed. Run each query in its own run_sql call.",
            only_queries,
        )
    tree = statements[0]
    # A Query is a SELECT, a set operation such as UNION, or either of them under a WITH clause.
    if not isinstance(tree, exp.Query):
        raise SqlRejected("not_select", "Only SELECT queries are allowed.", False)
    return tree


def _reject_forbidden_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        # A write hidden anywhere, for example inside a WITH clause.
        if isinstance(node, _WRITE_NODES):
            raise SqlRejected("not_select", "Only SELECT queries are allowed.", False)
        # FROM something that is not a table, a subquery or UNNEST: a table function.
        if isinstance(node, exp.From | exp.Join) and not isinstance(node.this, _FROM_SOURCES):
            raise SqlRejected(
                "table_function", "FROM may only use tables, subqueries and UNNEST.", False
            )
        # `namespace.function(...)`: how user-defined, remote, ML and AI functions are called.
        if isinstance(node, exp.Dot) and isinstance(node.expression, exp.Func):
            namespace = node.this.name.upper() if isinstance(node.this, exp.Identifier) else ""
            if namespace not in ALLOWED_FUNCTION_NAMESPACES:
                raise _forbidden_function(node.sql(dialect="bigquery").split("(")[0])
        # The same call when the parser kept the dotted name in one piece, and the functions
        # that are forbidden by name.
        if isinstance(node, exp.Func):
            name = _function_name(node)
            if "." in name or name in FORBIDDEN_FUNCTIONS:
                raise _forbidden_function(name)
        # SESSION_USER written without parentheses parses as a column.
        if isinstance(node, exp.Column) and node.name.upper() in FORBIDDEN_FUNCTIONS:
            raise _forbidden_function(node.name.upper())
        # Query parameters and system variables (@x, @@project_id). No analysis needs them, and a
        # system variable can reveal the project the service runs in.
        if isinstance(node, _PARAMETERS):
            raise SqlRejected(
                "parameter", "Parameters and system variables are not allowed.", False
            )


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):  # a function the parser does not know by name
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
    # References are collected by identity, not by name: the same name can be a WITH name in one
    # part of the query and a real table in another. A qualified name is never a WITH name.
    with_refs: set[int] = set()
    for scope in scopes:
        visible = {name.lower() for name in scope.cte_sources}
        with_refs.update(id(t) for t in scope.tables if not t.db and t.name.lower() in visible)

    # A WITH clause named after a real table could stand in for the table that the scope filter
    # reads, so those names are reserved.
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
        # Either the plain name, or the name qualified with our own dataset. Another dataset or
        # project is refused even when the table name matches.
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
    # Every identifier is checked, not only column references: a personal data column can also
    # be named in USING (...) or as a field of a row value.
    used = sorted({i.name.lower() for i in tree.find_all(exp.Identifier)} & PII_COLUMNS)
    if used:
        raise SqlRejected(
            "pii_column",
            f"Personal data columns cannot be used: {', '.join(used)}. "
            "Identify customers by id. Age, gender, city, state and country may be used.",
            True,
        )


def _enforce_limit(tree: exp.Expression, max_rows: int) -> tuple[exp.Expression, int]:
    """Keep a smaller literal LIMIT; replace a missing, larger or computed one."""
    limit = tree.args.get("limit")
    value = limit.expression if limit is not None else None
    if isinstance(value, exp.Literal) and value.is_int and int(value.name) <= max_rows:
        return tree, int(value.name)
    return tree.limit(max_rows), max_rows
