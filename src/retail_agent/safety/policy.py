"""What the agent may touch. Everything the safety layer enforces is defined here."""

from __future__ import annotations

from retail_agent.data.schema import TABLES

# Exactly the four tables named in the brief. The dataset has more (events, inventory_items,
# distribution_centers); the agent cannot reach them.
ALLOWED_TABLES = frozenset(TABLES)

# Direct identifiers and quasi-identifiers. These never leave the database layer.
PII_COLUMNS = frozenset(
    {"first_name", "last_name", "email", "street_address", "postal_code", "latitude", "longitude"}
)

# The `users` columns an analyst may see. Customers are identified by the pseudonymous `id`.
SAFE_USER_COLUMNS = tuple(c.name for c in TABLES["users"] if c.name not in PII_COLUMNS)

# Functions that reveal the service identity or reach outside the allowed tables.
FORBIDDEN_FUNCTIONS = frozenset(
    {"SESSION_USER", "CURRENT_USER", "EXTERNAL_QUERY", "EXTERNAL_OBJECT_TRANSFORM"}
)

# `namespace.function()` is how BigQuery calls user-defined, remote, ML and AI functions, which
# can run code or read tables outside this gate. Only these built-in namespaces are allowed.
# (SAFE. and NET. are parsed as built-ins and need no entry here.)
ALLOWED_FUNCTION_NAMESPACES = frozenset({"HLL_COUNT"})
