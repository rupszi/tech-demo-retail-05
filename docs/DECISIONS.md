# Decision log

This document records the decisions made while building the project, in the order they were made, with the reasoning behind each one. It is written for a reader who was not in the room: every entry says what the situation was, what was decided, what else was considered, what it costs, and how it is checked.

It is updated in the same commit as the work it describes. If a decision looks wrong or a reason is unclear, please ask or challenge it: each entry names the code and tests that implement it, so a question can be answered by pointing at something concrete. Questions that only the client can answer are tracked in [TRACKER.md](TRACKER.md#open-questions-for-the-client).

Related documents: [PLAN.md](PLAN.md) (scope, phases, exit gates), [TRACKER.md](TRACKER.md) (progress).

## Index

| ID | Decision | Status |
|---|---|---|
| D-01 | One data interface, two engines: DuckDB locally, BigQuery for real | Implemented |
| D-02 | The table schema lives in code and is verified against the live dataset | Implemented |
| D-03 | Mock data is fictional, deterministic, and contains planted patterns | Implemented |
| D-04 | Data errors are classified by whether a retry can help | Implemented |
| D-05 | Every BigQuery query is dry-run first and capped in cost | Implemented |
| D-06 | Safety is enforced in code on the parsed SQL, not in the prompt | Implemented |
| D-07 | Only SQL regenerated from the syntax tree is executed | Implemented |
| D-08 | Per-user product scope is applied by rewriting table references | Implemented |
| D-09 | Personal data is kept out by a column allow-list; customers are shown by ID | Implemented |
| D-10 | Tables can only be reached by fully qualified name | Implemented |
| D-11 | Namespaced functions are denied by default | Implemented |
| D-12 | Results are row-limited and say when they were cut | Implemented |
| D-13 | Output is scrubbed for personal data as a second layer | Implemented |
| D-14 | A cheap rule-based guard runs before any model call | Implemented |
| D-15 | The agent reaches data only through one gateway object | Implemented |
| D-16 | User profiles are files, one per data backend | Implemented |
| D-17 | LangGraph for orchestration, Gemini through the `google-genai` SDK | Decided, phase 3-4 |
| D-18 | Tooling: `uv`, Python 3.12, `ruff`, `pytest` | Implemented |
| D-19 | Secrets stay in a local `.env`; nothing sensitive is committed | Implemented |

---

## Foundations

### D-01. One data interface, two engines

**Situation.** The brief requires BigQuery. Developing only against BigQuery would make every test need network access, credentials and a cloud project, and would make tests slow and impossible to run on a reviewer's machine without setup.

**Decision.** All data access goes through one small interface, `DataBackend` (`list_tables`, `get_schema`, `dry_run`, `execute`). There are two implementations: `DuckDBBackend` over a local mock database, and `BigQueryBackend` over the real dataset. The agent writes BigQuery SQL in both cases; the DuckDB backend translates it with `sqlglot`.

**Why.** The whole safety layer, the delete flow and the tracing can be developed and tested offline in about two seconds. BigQuery becomes a configuration switch (`DATA_BACKEND=bigquery`) rather than a rewrite. The same interface is also the extension point the brief asks for: a new data source is a new class.

**Considered instead.** Mocking the BigQuery client in tests: this checks that we call the client, not that queries return the right rows. The BigQuery emulator: an extra service to install and not fully compatible. SQLite: weaker SQL dialect and worse translation from BigQuery syntax than DuckDB.

**Cost.** A few BigQuery functions do not translate to DuckDB. Those queries work in production and fail locally; the valid-query test suite shows which constructs are covered on both.

**Where.** `src/retail_agent/data/base.py`, `duckdb_backend.py`, `bigquery_backend.py`. Tests: `tests/test_backends.py`.

### D-02. The schema lives in code and is verified against the live dataset

**Decision.** `data/schema.py` defines the four tables, their columns, types and descriptions. The mock database is created from it, and it is what the agent is told about the data.

**Why.** One source of truth means the mock cannot drift from what the safety layer and the prompts assume. Descriptions are written for the model ("Price the customer paid for the item (revenue)"), which the live schema does not provide.

**Verified.** On 2026-10-04 the definitions were compared with the live tables: all columns, types and column order match. The only difference is deliberate: `users.user_geom` is left out because it duplicates latitude and longitude, which are personal data.

**Worth knowing.** The live dataset also contains `events`, `inventory_items` and `distribution_centers`. They are outside the brief and the agent must not reach them (see D-10).

### D-03. Mock data is fictional, deterministic, and contains planted patterns

**Decision.** `data/mock.py` generates about 600 customers, 90 products, 2,500 orders and 5,000 order items from a seed. Brand and customer names are invented. Two patterns are planted on purpose: customers in Texas buy fewer items at lower prices, and the brand "Driftline" has a return rate about three times that of the other brands (roughly 30% against 10%).

**Why.** The brief's example questions are "why" questions ("why are users in state X underspending"). An agent can only be tested on those if the data contains an answer that we know in advance. Determinism means a failing test is a real regression and not noise. Invented brands avoid attaching made-up numbers to real companies.

**Detail.** In tests the data ends on a fixed date so results are reproducible. The local database file ends on the day it is built, like the real dataset (which is refreshed up to the current day), so "last month" means the same thing locally and in BigQuery.

**Where.** Tests: `tests/test_mock_data.py` checks determinism, schema match, referential integrity and that both planted patterns are present.

### D-04. Data errors are classified by whether a retry can help

**Decision.** Backends raise one error type, `DataError`, with a `kind`:

| Kind | Meaning | What the agent should do |
|---|---|---|
| `syntax` | Bad SQL, unknown table or column | Show the error to the model and let it rewrite the query |
| `execution` | Failed while running | The model may be able to rewrite |
| `too_expensive` | Scan limit exceeded | The model must narrow the query |
| `unavailable` | Backend down, throttled or timed out | Retry the same SQL later; rewriting will not help |

**Why.** The brief asks for self-correction "without inflating costs". The cheapest way to waste money is to ask the model to rewrite a correct query because the database had a hiccup, or to resend the same broken query. The classification is what lets the retry logic (phase 3-4) choose the right response.

**Where.** `data/base.py`; mapping from Google API errors in `bigquery_backend.py`. Tests: `test_bq_error_classification`.

### D-05. Every BigQuery query is dry-run first and capped in cost

**Decision.** The BigQuery backend extends the runner supplied with the brief with: a dry-run before execution, `maximum_bytes_billed` on every job, and a timeout.

**Why.** A dry-run is free and reports both errors and the bytes a query would scan. So most broken queries are caught without paying for them, and an oversized query is refused before it runs. The byte cap is a hard stop on the BigQuery side even if our own check were skipped. The supplied runner had none of these.

**Where.** `bigquery_backend.py`. Tests use a stubbed client so they need no network.

---

## Safety

The brief has three safety requirements: only analysis questions, no personal data in output, and each user sees only data for their own products. The decisions below implement them. The overall principle is in D-06 and the rest follow from it.

### D-06. Safety is enforced in code on the parsed SQL, not in the prompt

**Situation.** The simplest way to build this is to tell the model "never select emails, and only query the user's brands". This fails in two ways: a user can talk the model out of its instructions (prompt injection), and a model can simply make a mistake.

**Decision.** The model's SQL is treated as untrusted input. Before any query runs, `validate_query` parses it into a syntax tree, checks it against allow-lists, rewrites it for the user's permissions, and only then lets it through. The rules are default-deny: what is not recognised as safe is rejected.

**Why.** A rule enforced in code holds no matter what the user typed or what the model generated. The prompt still tells the model the rules, but only so it wastes fewer attempts; nothing depends on the model obeying.

**What is checked.**

| Rule | Example of what it stops |
|---|---|
| Exactly one statement, and it must be a query | `DROP TABLE`, `SELECT 1; DELETE ...`, scripts, `EXPORT DATA` |
| No write operation anywhere in the tree | A `DELETE` hidden inside a `WITH` clause |
| `FROM` may only use allowed tables, subqueries and `UNNEST` | `EXTERNAL_QUERY(...)`, `ML.PREDICT(...)`, table functions |
| Only the four allowed tables | `events`, `INFORMATION_SCHEMA`, other datasets and projects, wildcard tables |
| No personal data columns | `email`, `first_name` and so on, by any alias or path |
| No namespaced functions (D-11) | User-defined, remote, ML and AI functions |
| Row limit (D-12) | A query that returns the whole table |

**Rejections tell the agent what to do next.** Each rejection has a code and a `retryable` flag. Honest mistakes (a syntax error, an unknown table, a personal data column) are retryable: the message is given back to the model so it can fix the query. Things a well-behaved model would not write (a write statement, several statements, a forbidden function) are not retryable, so no further model calls are spent on them.

**Considered instead.** Checking the SQL text with regular expressions: easy to bypass with comments, casing, quoting or nesting. Relying only on database permissions: necessary in production (see "Known limits"), but the brief's public dataset cannot be given row-level policies, and permissions alone give the model no useful feedback.

**Where.** `safety/validator.py`. Tests: `tests/test_sql_gate.py` with the corpora in `tests/sql_cases.py`: 73 hostile queries are all rejected and 24 legitimate analytical queries all pass, for each of the four user profiles.

### D-07. Only SQL regenerated from the syntax tree is executed

**Decision.** The text the model wrote is never sent to the database. After validation and rewriting, the SQL is generated again from the syntax tree, without comments, and that is what runs.

**Why.** It removes a whole class of tricks: anything hidden in a comment, unusual whitespace or an odd quoting style does not survive the round trip. It also means the query that was checked and the query that runs are the same object, so there is no gap between them.

**Where.** Last line of `validate_query`. Test: `test_comments_and_hidden_text_do_not_survive`.

### D-08. Per-user product scope is applied by rewriting table references

**Situation.** "Each user should only be able to analyse data on products related to him." The data model has no ownership column, so "related" has to be defined. The working assumption (to confirm with the client) is that each user has a list of allowed brands and/or departments.

**Decision.** Every reference to a real table in the query is replaced by a subquery that only contains what the user may see:

| Table | What the user sees |
|---|---|
| `products` | Products of their brands/departments |
| `order_items` | Items whose product is in scope |
| `orders` | Orders containing at least one in-scope item |
| `users` | Customers who bought at least one in-scope product, without personal data columns |

For example, when a user limited to three brands writes `SELECT COUNT(*) FROM order_items`, the query that runs is:

```sql
SELECT COUNT(*) FROM (
  SELECT id, order_id, user_id, product_id, ..., sale_price
  FROM `bigquery-public-data`.thelook_ecommerce.order_items
  WHERE product_id IN (
    SELECT id FROM `bigquery-public-data`.thelook_ecommerce.products
    WHERE brand IN ('Allegra K', 'Levi\'s', 'Roxy'))
) AS order_items LIMIT 500
```

**Why.** The filter is attached to the table itself, so it applies wherever the table is used: in joins, subqueries, unions, `WITH` clauses, or behind an alias. There is nothing for the model to remember and nothing a user can negotiate away. The model writes ordinary SQL and does not need to know the filter exists.

**Considered instead.** Asking the model to add `WHERE brand IN (...)`: unenforceable. Appending a `WHERE` clause to the outer query: wrong for queries with subqueries or joins, and easy to escape with `OR TRUE`. Database row-level security: the right second layer in production, but not possible on a public dataset we do not own.

**Trade-offs to be aware of.** An order that mixes in-scope and out-of-scope products is visible, and its `num_of_item` counts all its items; revenue is always computed from `order_items`, which is fully scoped. A user with no restrictions (for example the CEO profile) gets the tables unfiltered, but `users` is still stripped of personal data.

**Where.** `safety/scoping.py`. Tests: `tests/test_scoping.py` compares what each user receives with the expected rows computed independently in pandas, and runs 18 queries written specifically to escape the scope; none returns a product outside it. A separate test confirms the scopes differ, so the comparison cannot pass by accident.

### D-09. Personal data is kept out by a column allow-list; customers are shown by ID

**Decision.** Seven `users` columns are classed as personal data and never leave the database: `first_name`, `last_name`, `email`, `street_address`, `postal_code`, `latitude`, `longitude`. The remaining columns (`id`, `age`, `gender`, `state`, `city`, `country`, `traffic_source`, `created_at`) may be used. Customers are identified by `id`.

There are two mechanisms, and it matters which one is the guarantee:

1. **The guarantee.** The real `users` table only ever appears inside the scoping subquery from D-08, and that subquery selects the safe columns by name. So `SELECT *`, whole-row expressions such as `SELECT u FROM users u`, and `TO_JSON_STRING(u)` can only ever see safe columns. A query that names a personal data column in some way the check below misses fails in the database, because the column does not exist in what it is querying.
2. **The convenience.** Queries that name a personal data column are rejected up front with a message the model can act on ("identify customers by id"). This avoids a wasted database call and gives a clearer error.

**Why an allow-list of columns rather than masking values.** Masking (showing `m***@example.com`) still returns something derived from the personal value and has to be right for every function that could touch it. Not selecting the column at all has no such edge cases. The brief's required capability "top customers" still works, with customers shown as IDs.

**Open point for the client.** `postal_code` is treated as personal data because, combined with age and gender, it can identify a person. Row-level demographics (one row per customer ID with age, gender and city) are currently allowed. If the client wants demographics only in aggregate, the production answer is a minimum group size (see "Known limits").

**Where.** `safety/policy.py`. Tests: every personal data column is tried through four access paths; whole-row tricks are run against the mock data and the output is searched for real email addresses, street addresses and coordinates.

### D-10. Tables can only be reached by fully qualified name

**Situation.** While testing the first version of the gate, a bypass was found. SQL lets a query define a temporary name in a `WITH` clause, and such a name is only valid in part of the query. The first version treated any table name that matched a `WITH` name as harmless, anywhere in the query. So this query read the `events` table, which is not on the allow-list:

```sql
SELECT * FROM events            -- the real table: the WITH name is not visible here
WHERE 1 IN (WITH events AS (SELECT 1 AS x) SELECT x FROM events)
```

A second variant used a `WITH` name defined later in the same clause. A third went the other way: defining `WITH products AS (...)` to replace the table that the scope filter from D-08 relies on.

**Decision.** Three changes, each of which closes the hole on its own:

1. **Scope analysis.** A table reference is treated as a `WITH` name only if the SQL parser's scope analysis shows that name is visible at that exact point in the query. Every other reference is a real table and must be on the allow-list.
2. **No default dataset.** BigQuery jobs run without a default dataset, so a bare name such as `events` resolves to nothing. The gate itself writes the only fully qualified table names in the final SQL. The local DuckDB database is attached under the same `project.dataset` path, so it behaves the same way and the tests exercise the real rule.
3. **No reuse of table names.** A `WITH` clause may not be named after one of the four tables.

**Why three.** The first is the precise fix. The second means that even a future mistake in the first cannot expose a table, because the database will not resolve the name. The third protects the scope filter's own subqueries.

**Verified.** The test corpus contains each variant. On 2026-10-04 a bare-name query was sent directly to BigQuery and was refused with "Table "events" must be qualified with a dataset".

**Where.** `_real_tables` in `safety/validator.py`; `bigquery_backend.py` (`_config`); `duckdb_backend.py` (`ATTACH`).

### D-11. Namespaced functions are denied by default

**Decision.** A function call written as `something.function(...)` is rejected unless the namespace is a known BigQuery built-in. `SESSION_USER`, `CURRENT_USER` and `EXTERNAL_QUERY` are rejected by name.

**Why.** In BigQuery the dotted form is how user-defined functions, remote functions, and the ML and AI functions are called. A user-defined function can read tables on its own, outside the gate; a remote function calls an external service; AI functions cost money per row. Ordinary analytical functions (`SUM`, `DATE_TRUNC`, `SAFE_DIVIDE`, window functions and so on) are not namespaced and are unaffected.

**Trade-off.** A legitimate built-in namespace that is not on the short allow-list would be rejected until it is added. That is the intended direction of failure.

**Where.** `safety/policy.py` (`ALLOWED_FUNCTION_NAMESPACES`, `FORBIDDEN_FUNCTIONS`).

### D-12. Results are row-limited and say when they were cut

**Decision.** Every query gets a `LIMIT` (500 rows by default). An existing smaller limit is kept; a missing or larger one is replaced. The result object carries a `truncated` flag that is true when the limit was reached.

**Why.** The limit bounds what is sent to the model (cost) and what could leak in the worst case. The flag exists because a silent cut is dangerous in analysis: a report built on the first 500 rows of a larger result would be confidently wrong. With the flag, the agent can tell the model to aggregate further instead.

**Where.** `_enforce_limit` in `validator.py`; `QueryResult.truncated` in `gateway.py`.

### D-13. Output is scrubbed for personal data as a second layer

**Decision.** Query results pass through a scrubber that masks emails, phone numbers, street addresses, coordinates, card numbers and social security numbers, and counts what it masked. The agent's final answers and saved reports go through the same function (wired in phase 4).

**Why.** D-09 should mean there is nothing to scrub. The scrubber is there so that a mistake in that layer, or personal data a user types into the conversation, still does not reach the screen or a saved report. The counts feed observability: a non-zero count is a signal that something upstream is wrong.

**Limits, stated plainly.** It is pattern based. It cannot recognise a person's name, and street suffixes that are also common words ("Drive", "Way", "Place") are left out on purpose, because masking a heading such as "3 Key Factors Drive Growth" would damage reports. It is a backstop, not the control. In production the same hook would call a managed inspection service with proper detectors.

**Where.** `safety/scrubber.py`. Tests include business text that must not be altered.

### D-14. A cheap rule-based guard runs before any model call

**Decision.** User input is first checked by a small set of rules: prompt injection phrases, requests for personal data, probing for credentials, clearly unrelated requests, and over-long input. A blocked message gets a fixed, helpful reply and costs no tokens. Text is normalised first, so zero-width characters and look-alike letters do not slip through.

**Why.** It answers the obvious cases instantly and for free. It is deliberately not the security boundary: a message that gets past it still cannot reach personal data or another user's products, because of D-06 to D-10. That is why the rules can be conservative and tuned to avoid blocking real questions.

**What it does not do.** It cannot judge meaning. Deciding that a politely worded question is off-topic is the job of the intent router in phase 4, which uses the model.

**Where.** `safety/guard.py`. Tests: 34 messages that must be blocked and 23 realistic executive questions that must not be, including look-alikes such as "customers acquired via Email" and "delete all reports mentioning Driftline".

### D-15. The agent reaches data only through one gateway object

**Decision.** The agent is given a `QueryGateway`, never a database backend. `gateway.run(sql)` validates, scopes, dry-runs, executes and scrubs, in that order.

**Why.** If the agent's code had the backend, safety would depend on every future code path remembering to call the validator. With the gateway, there is no way to run a query that skips a step, and a new tool that needs data gets the same protection automatically.

**Where.** `safety/gateway.py`.

### D-16. User profiles are files, one per data backend

**Decision.** Who the user is and what they may see comes from `config/users.<backend>.json`. Four demo users cover the cases: two restricted to different brands, one restricted to a department, one unrestricted.

**Why.** In production, identity comes from single sign-on and permissions from an entitlements service; a file is the simplest stand-in with the same shape. There are two files because the mock data uses invented brands and the real dataset has real ones.

---

## Agent and tooling

### D-17. LangGraph for orchestration, Gemini through the `google-genai` SDK

**Status.** Decided; implemented in phases 3 and 4.

**Decision.** The conversation flow is a LangGraph graph. The model is Gemini, called through Google's `google-genai` SDK behind a small interface of our own, with a fake implementation for tests.

**Why LangGraph.** Two requirements shape the choice. The delete flow needs execution to stop, wait for a human, and resume exactly where it left off; LangGraph's interrupt and checkpoint mechanism does precisely that. Observability needs to know which step ran, for how long, and with what result; a graph of named steps gives that structure for free. The flow is also explicit and readable as a diagram, which suits a system that has to be explained and audited.

**Considered instead.** A hand-written loop around the SDK: least dependency, but the pause-and-resume and state persistence would have to be built and tested by hand. Google's Agent Development Kit: a natural fit for Gemini, but more opinionated about deployment and less direct for a custom confirmation step. LangChain's prebuilt agents: hide the control flow that this design needs to make explicit.

**Experience level.** This is the author's first project with LangGraph. It was chosen for the fit described above and learned for this assignment.

**Model access.** The prototype uses an API key from Google AI Studio, as the brief suggests. The model client will also accept Application Default Credentials through Vertex AI (phase 3), which is what production should use: no long-lived key, and enterprise data terms. At the time of writing, the free tier's terms allow submitted content to be used for product improvement, so it is appropriate for this public dataset only; the terms should be checked before any real data is used.

### D-18. Tooling

`uv` for environments and locking (one command to reproduce the setup on another machine), Python 3.12 (broad library support), `ruff` for lint and format, `pytest` for tests. The full suite runs offline in about two seconds, which keeps the edit-test loop short.

### D-19. Secrets

The API key and project ID live in a local `.env` that is git-ignored and has never been committed. `.env.example` documents every variable without values.

---

## Known limits, and what production adds

These are stated so nobody has to discover them.

| Area | Limit in the prototype | Production answer |
|---|---|---|
| Scope enforcement | Enforced by the application only | Add BigQuery row-level security or authorized views, so the database enforces the same rule independently |
| Personal data | Column allow-list plus pattern scrubber | Add column-level policy tags, and a managed inspection service in place of the patterns |
| Demographics | Row-level safe columns are allowed | Minimum group size for demographic breakdowns |
| Input guard | Rules only | Semantic classification by the router (phase 4), plus a managed prompt-safety service |
| Identity | Chosen with a command-line flag | Single sign-on; profile from an entitlements service |
| Local engine | Some BigQuery functions do not translate | Not relevant: production uses BigQuery |

## Verification against the real dataset

Run on 2026-10-04 with the project `opsfleet-demo`:

- The schema in code matches the live tables (D-02).
- All 24 legitimate test queries, after rewriting for each of the four profiles, pass a BigQuery dry-run: 96 of 96. Dry-runs are free.
- A bare table name sent straight to BigQuery is refused (D-10).
- With real brands, each restricted profile sees only its own brands, including a brand name containing an apostrophe, which confirms values are escaped correctly.
- `SELECT * FROM users` on real data returns only the eight safe columns.
