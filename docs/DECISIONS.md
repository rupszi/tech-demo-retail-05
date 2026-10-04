# Decision log

This document records the decisions made while building the project, in the order they were made, with the reasoning behind each one. It is written for a reader who was not in the room: every entry says what the situation was, what was decided, what else was considered, what it costs, and how it is checked.

It is updated with the work it describes. On 2026-10-04 the client answered our questions ([QUESTIONS.md](QUESTIONS.md)); entries that changed because of an answer say so. If a decision looks wrong or a reason is unclear, please ask or challenge it: each entry names the code and tests that implement it, so a question can be answered by pointing at something concrete. Questions that only the client can answer are in [QUESTIONS.md](QUESTIONS.md), with the assumption used for each.

Related documents: [DESIGN.md](DESIGN.md) (how the system works), [PLAN.md](PLAN.md) (scope, phases, exit gates), [TRACKER.md](TRACKER.md) (progress).

## Index

| ID | Decision | Status |
|---|---|---|
| D-01 | One data interface, two engines: BigQuery for real, DuckDB for offline tests | Implemented |
| D-02 | The table schema lives in code and is verified against the live dataset | Implemented |
| D-03 | Mock data is fictional, deterministic, and contains planted patterns | Implemented |
| D-04 | Data errors are classified by whether a retry can help | Implemented |
| D-05 | Every BigQuery query is dry-run first and capped in cost | Implemented |
| D-06 | Safety is enforced in code on the parsed SQL, not in the prompt | Implemented |
| D-07 | Only SQL regenerated from the syntax tree is executed | Implemented |
| D-08 | Per-user brand scope is applied by rewriting table references | Implemented; scope confirmed by the client |
| D-09 | Personal data is kept out by a column allow-list; customers are shown by ID | Implemented; confirmed by the client |
| D-10 | Tables can only be reached by fully qualified name | Implemented |
| D-11 | Namespaced functions are denied by default | Implemented |
| D-12 | Results are row-limited and say when they were cut | Implemented |
| D-13 | Output is scrubbed for personal data as a second layer | Implemented |
| D-14 | A cheap rule-based guard runs before any model call | Implemented |
| D-15 | The agent reaches data only through one gateway object | Implemented |
| D-16 | A user's profile is built from token claims, and access is denied by default | Implemented; changed after the client's answer |
| D-17 | LangGraph for orchestration, Gemini through the `google-genai` SDK | Implemented |
| D-18 | Tooling: `uv`, Python 3.12, `ruff`, `pytest` | Implemented |
| D-19 | Secrets stay in a local `.env`; nothing sensitive is committed | Implemented |
| D-20 | One agent loop with tools, not a fixed pipeline | Implemented |
| D-21 | Self-correction has a budget, and not every failure earns a retry | Implemented |
| D-22 | Models are an ordered list; a rate-limited model is rested | Implemented |
| D-23 | A delete is prepared by the model, decided by the user, permanent, and reported by the application | Implemented; changed after the client's answer |
| D-24 | Earlier result tables are not resent to the model | Implemented |
| D-25 | Date and revenue conventions are written down, and totals come from SQL | Implemented |
| D-26 | The Golden bucket is a folder in the prototype | Implemented, confirmed by the client |
| D-27 | Traces are one JSON line per question; metrics are computed from them | Implemented |
| D-28 | What was deliberately left out of the prototype | Decided |
| D-29 | BigQuery is the default data source, and has its own test group | Implemented; follows the client's suggestion |
| D-30 | The tests are checked by breaking the code on purpose | Done |

---

## Foundations

### D-01. One data interface, two engines

**Situation.** The brief requires BigQuery. Developing only against BigQuery would make every test need network access, credentials and a cloud project, and would make tests slow and impossible to run on a reviewer's machine without setup.

**Decision.** All data access goes through one small interface, `DataBackend` (`list_tables`, `get_schema`, `dry_run`, `execute`). There are two implementations: `DuckDBBackend` over a local mock database, and `BigQueryBackend` over the real dataset. The agent writes BigQuery SQL in both cases; the DuckDB backend translates it with `sqlglot`.

**Why.** The whole safety layer, the delete flow and the tracing can be developed and tested offline in about three seconds. The assistant itself runs on BigQuery by default; `--backend duckdb` switches it to the mock (D-29). The same interface is also the extension point the brief asks for: a new data source is a new class.

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
| `too_expensive` | Scan limit exceeded, or the query ran past its time limit | The model must narrow the query |
| `unavailable` | Backend down or throttled, or its credentials expired | Retry the same SQL later; rewriting will not help |

**Why.** The brief asks for self-correction "without inflating costs". The cheapest way to waste money is to ask the model to rewrite a correct query because the database had a hiccup, or to resend the same broken query. The classification is what lets the retry logic (D-21) choose the right response.

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
| No query parameters or system variables | `@@project_id`, which would name the project the service runs in |
| Row limit (D-12) | A query that returns the whole table |

**Rejections tell the agent what to do next.** Each rejection has a code and a `retryable` flag. Honest mistakes (a syntax error, an unknown table, a personal data column) are retryable: the message is given back to the model so it can fix the query. Things a well-behaved model would not write (a write statement, a forbidden function, a script) are not retryable, so no further model calls are spent on them. Several plain queries in one call are the exception among multi-statement input: a real run showed a model doing that to save a step, so it is told to send one query per call and may try again.

**Considered instead.** Checking the SQL text with regular expressions: easy to bypass with comments, casing, quoting or nesting. Relying only on database permissions: necessary in production (see "Known limits"), but the brief's public dataset cannot be given row-level policies, and permissions alone give the model no useful feedback.

**Where.** `safety/validator.py`. Tests: `tests/test_sql_gate.py` with the corpora in `tests/sql_cases.py`: 80 hostile queries are all rejected and 24 legitimate analytical queries all pass, for each of the three user profiles.

### D-07. Only SQL regenerated from the syntax tree is executed

**Decision.** The text the model wrote is never sent to the database. After validation and rewriting, the SQL is generated again from the syntax tree, without comments, and that is what runs.

**Why.** It removes a whole class of tricks: anything hidden in a comment, unusual whitespace or an odd quoting style does not survive the round trip. It also means the query that was checked and the query that runs are the same object, so there is no gap between them.

**Where.** Last line of `validate_query`. Test: `test_comments_and_hidden_text_do_not_survive`.

### D-08. Per-user brand scope is applied by rewriting table references

**Situation.** "Each user should only be able to analyse data on products related to him." The data model has no ownership column, so "related" had to be defined. The client's answer: each user sees only the brands related to them, and the CEO sees all.

**Decision.** Every reference to a real table in the query is replaced by a subquery that only contains what the user may see:

| Table | What the user sees |
|---|---|
| `products` | Products of their brands |
| `order_items` | Items whose product is one of their brands |
| `orders` | Orders containing at least one such item |
| `users` | Customers who bought at least one such product, without personal data columns |

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

**Considered instead.** Asking the model to add `WHERE brand IN (...)`: unenforceable. Appending a `WHERE` clause to the outer query: wrong for queries with subqueries or joins, and easy to escape with `OR TRUE`. Row policies in BigQuery: these need BigQuery to know who the end user is, and the scopes arrive in an application-level token that BigQuery never sees (D-16).

**What changed after the client's answer.** The first version could also scope by department, and had a demo user for it. Nobody asked for that, so it was removed: brand is the only scope.

**Trade-offs to be aware of.** An order that mixes a user's brands with other brands is visible, and its `num_of_item` counts all its items; revenue is always computed from `order_items`, which is fully scoped. The client was asked about this and did not comment, so it stands. A user with the all-brands grant gets the tables unfiltered, but `users` is still stripped of personal data.

**Where.** `safety/scoping.py`. Tests: `tests/test_scoping.py` compares what each user receives with the expected rows computed independently in pandas, and runs 18 queries written specifically to escape the scope; none returns a product outside it. A separate test confirms the scopes differ, so the comparison cannot pass by accident. The BigQuery test group repeats the check on the real dataset.

### D-09. Personal data is kept out by a column allow-list; customers are shown by ID

**Decision.** Seven `users` columns are classed as personal data and never leave the database: `first_name`, `last_name`, `email`, `street_address`, `postal_code`, `latitude`, `longitude`. The remaining columns (`id`, `age`, `gender`, `state`, `city`, `country`, `traffic_source`, `created_at`) may be used. Customers are identified by `id`.

There are two mechanisms, and it matters which one is the guarantee:

1. **The guarantee.** The real `users` table only ever appears inside the scoping subquery from D-08, and that subquery selects the safe columns by name. So `SELECT *`, whole-row expressions such as `SELECT u FROM users u`, and `TO_JSON_STRING(u)` can only ever see safe columns. A query that names a personal data column in some way the check below misses fails in the database, because the column does not exist in what it is querying.
2. **The convenience.** Queries that name a personal data column anywhere, as a column, in `USING (...)` or as a field of a row value, are rejected up front with a message the model can act on ("identify customers by id"). This avoids a wasted database call and gives a clearer error.

**Why an allow-list of columns rather than masking values.** Masking (showing `m***@example.com`) still returns something derived from the personal value and has to be right for every function that could touch it. Not selecting the column at all has no such edge cases. The brief's required capability "top customers" still works, with customers shown as IDs.

**Confirmed by the client.** The list of personal data is theirs: names, email, address, postal code, coordinates. Identifying customers by ID is fine. Showing age, gender, city, state and country for an individual customer is fine, so no minimum group size is needed.

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

**Decision.** Every query gets a `LIMIT` (500 rows by default). An existing smaller limit is kept; a missing or larger one is replaced. The result object carries a `truncated` flag that is true when our row limit was reached. A smaller limit that the query chose itself, such as a top 5, is a complete answer and is not flagged; the first version flagged those too, which told the model on every top-N query that its result was incomplete.

**Why.** The limit bounds what is sent to the model (cost) and what could leak in the worst case. The flag exists because a silent cut is dangerous in analysis: a report built on the first 500 rows of a larger result would be confidently wrong. With the flag, the agent can tell the model to aggregate further instead.

**Where.** `_enforce_limit` in `validator.py`; `QueryResult.truncated` in `gateway.py`.

### D-13. Output is scrubbed for personal data as a second layer

**Decision.** Query results pass through a scrubber that masks emails, phone numbers, street addresses, coordinates, card numbers and social security numbers, and counts what it masked. The agent's final answers and saved reports, titles included, go through the same function. Everything written to a trace or to the audit log is scrubbed as well, not only the question and the answer: the SQL the model wrote, or the criteria of a delete request, can repeat something a user typed.

**Why.** D-09 should mean there is nothing to scrub. The scrubber is there so that a mistake in that layer, or personal data a user types into the conversation, still does not reach the screen or a saved report. The counts feed observability: a non-zero count is a signal that something upstream is wrong.

**Limits, stated plainly.** It is pattern based. It cannot recognise a person's name, and street suffixes that are also common words ("Drive", "Way", "Place") are left out on purpose, because masking a heading such as "3 Key Factors Drive Growth" would damage reports. It is a backstop, not the control. In production the same hook would call a managed inspection service with proper detectors.

**Where.** `safety/scrubber.py`. Tests include business text that must not be altered.

### D-14. A cheap rule-based guard runs before any model call

**Decision.** User input is first checked by a small set of rules: prompt injection phrases, requests for personal data, probing for credentials, clearly unrelated requests, and over-long input. A blocked message gets a fixed, helpful reply and costs no tokens. Text is normalised first, so zero-width characters and the full-width or other compatibility forms of letters do not slip through. A letter borrowed from another alphabet, such as a Cyrillic "о", still does.

**Why.** It answers the obvious cases instantly and for free. It is deliberately not the security boundary: a message that gets past it still cannot reach personal data or another user's products, because of D-06 to D-10. That is why the rules can be conservative and tuned to avoid blocking real questions.

**What it does not do.** It cannot judge meaning. A politely worded off-topic question is left to the model, which is instructed to decline in one sentence without running a query (D-20).

**Where.** `safety/guard.py`. Tests: 35 messages that must be blocked and 24 realistic executive questions that must not be, including look-alikes such as "customers acquired via Email" and "delete all reports mentioning Driftline".

### D-15. The agent reaches data only through one gateway object

**Decision.** The agent is given a `QueryGateway`, never a database backend. `gateway.run(sql)` validates, scopes, dry-runs, executes and scrubs, in that order.

**Why.** If the agent's code had the backend, safety would depend on every future code path remembering to call the validator. With the gateway, there is no way to run a query that skips a step, and a new tool that needs data gets the same protection automatically.

**Where.** `safety/gateway.py`.

### D-16. A user's profile is built from token claims, and access is denied by default

**Situation.** The first version looked a user up in a file and treated a missing list of brands as "no restriction". The client then said that the front end sends a JWT with the user's scopes.

**Decision.** `UserProfile.from_claims` builds the profile from the claims of a verified token: `sub`, `name` and a `scopes` list with entries such as `brand:Levi's`.

- Seeing every brand takes the explicit scope `brand:*`, which is what the CEO's token carries.
- A token with no brand scope describes a user who may see nothing.
- Scopes of any other kind are ignored, so a token issued for something else grants nothing here.
- A token without a subject, or whose scopes are not a list of strings, is rejected.
- The wide grant must be written exactly as `brand:*`. Something close to it, such as `brand: *`, grants nothing.
- A sample file that lists the same user twice is an error, not a silent choice of the last entry.

**Why denied by default.** With scopes arriving in a token, "missing means everything" would turn a malformed or incomplete token into full access. Making the wide grant explicit means every mistake fails closed.

**The claim format is our assumption.** The client specified a JWT with scopes, not its layout. A `scopes` list of `kind:value` strings is a common shape and keeps brand names with spaces intact; the mapping is one function if the real token differs.

**In the prototype.** There is no front end to issue a token, so the files `config/users.<backend>.json` hold sample token payloads and `--user` picks one. Three users cover the cases: two with different brands, and the CEO. There are two files because the mock data uses invented brands and the real dataset has real ones. The signature check belongs to the API and is described in the design; the mapping from claims to access is real and tested.

**Where.** `safety/profiles.py`. Tests: `tests/test_profiles.py`, and `test_a_user_with_no_brand_scope_sees_nothing` in `tests/test_scoping.py`.

---

## Agent and tooling

### D-17. LangGraph for orchestration, Gemini through the `google-genai` SDK

**Decision.** The conversation flow is a LangGraph graph of four steps (D-20). The model is Gemini, called through Google's `google-genai` SDK behind a small interface of our own, with a scripted implementation for tests.

**Why LangGraph.** Two requirements shape the choice. The delete flow needs execution to stop, wait for a human, and resume exactly where it left off; LangGraph's interrupt and checkpoint mechanism does precisely that, and on resume only the confirmation step runs again. Observability needs to know which step ran, for how long, and with what result; a graph of named steps gives that structure. The flow is also explicit and readable as a diagram, which suits a system that has to be explained and audited.

**Considered instead.** A hand-written loop around the SDK: least dependency, but the pause-and-resume and state persistence would have to be built and tested by hand. Google's Agent Development Kit: a natural fit for Gemini, but more opinionated about deployment and less direct for a custom confirmation step. LangChain's prebuilt agents: hide the control flow that this design needs to make explicit.

**How much of it is used.** Only the core: a state graph, conditional edges, one interrupt and a checkpointer. The model is not called through LangChain's model wrappers. Messages in the graph state are plain dictionaries, so the state serialises without special handling and the provider can be swapped by writing one adapter.

**Tools are run by the graph, not by the SDK.** The SDK can execute tools automatically in a loop. That is switched off, because the graph must see every call: to check it, count it against the budget, trace it, and pause before a delete.

**Experience level.** This is the author's first project with LangGraph. It was chosen for the fit described above and learned for this assignment.

**Model access.** The prototype uses an API key from Google AI Studio, as the brief suggests. Setting `GEMINI_AUTH=vertex` makes the same client use Application Default Credentials through Vertex AI, which is what production should use: no long-lived key, and enterprise data terms. That switch is implemented but was not exercised here, because Vertex AI needs billing enabled. At the time of writing, the free tier's terms allow submitted content to be used for product improvement, so it is appropriate for this public dataset only; the terms should be checked before any real data is used.

**Where.** `agent/graph.py`, `llm/base.py`, `llm/gemini.py`.

### D-18. Tooling

`uv` for environments and locking (one command to reproduce the setup on another machine), Python 3.12 (broad library support), `ruff` for lint and format, `pytest` for tests. The offline suite runs in about three seconds, which keeps the edit-test loop short.

### D-19. Secrets

The API key and project ID live in a local `.env` that is git-ignored and has never been committed. `.env.example` documents every variable without values.

---

## The agent

These decisions were made while building and running the agent. Several of them come from watching real runs against BigQuery and Gemini, and the entries say what was observed.

### D-20. One agent loop with tools, not a fixed pipeline

**Situation.** There are two common shapes for this kind of system. A fixed pipeline classifies the question, writes SQL, runs it and writes the answer, each as a separate model call. An agent loop gives the model tools and lets it decide what to call until it can answer.

**Decision.** An agent loop: `guard -> agent <-> tools`, plus a separate step for confirming deletes. The model has five tools: `run_sql`, `save_report`, `list_reports`, `get_report`, `delete_reports`.

**Why.** The brief asks for multi-step analysis ("why are users in state X underspending, and how does that compare to state Y"). The number of queries such a question needs is not known in advance, and a loop handles one query or five without special cases. It also needs fewer model calls for simple questions: a question about the data's structure is answered in one call from the table descriptions, with no query. There is no separate "router" call: the model is told to decline unrelated questions, and what it can do is bounded by its tools either way.

**What keeps the loop safe.** Freedom to choose tools is not freedom to do harm: every tool is implemented by code that applies the rules (D-15, D-23), and the loop has a budget (D-21).

**Adding a tool.** A tool that needs only its arguments is a declaration, a method and a line in the toolbox's table of handlers, all in `agent/tools.py`; the graph does not change. `run_sql` and `delete_reports` are wired into the graph because one works within the limits of the question and the other goes through the confirmation step.

**Where.** `agent/graph.py`, `agent/tools.py`. Tests: `tests/test_agent.py`.

### D-21. Self-correction has a budget, and not every failure earns a retry

**Decision.** For each question the agent may correct a failed query twice (`MAX_SQL_RETRIES`). After that, no further query runs and the model is told to explain what it could not do. In addition:

| Situation | Response |
|---|---|
| Syntax error, unknown column, personal data column | Retry, with the error message given to the model |
| A write statement, several statements, a forbidden function | No retry |
| Empty result | A hint to check filter values, once; then the model is told to stop |
| The database is unavailable | The same SQL is retried once by the application; the model is not asked to rewrite it |
| The query runs out of time | The job is cancelled and the model must narrow the query; the same SQL is not run again |
| Several plain queries in one call | The model is told to send one per call; counts as one failure |
| One model call left for the question | The tool results tell the model to answer now from what it has |
| 8 model calls, 60,000 tokens or 120 seconds used on one question | Stop and ask the user to narrow the question |

**Why.** The brief asks for self-correction "before giving up" and "without inflating costs". Those pull in opposite directions, and a fixed budget is the honest way to satisfy both. The distinctions matter because the wrong response wastes money: rewriting a correct query when the database is down, or giving a second chance to a `DROP TABLE`.

**Why errors are cheap.** A parse error is caught by the SQL gate without touching BigQuery. A semantic error is caught by BigQuery's dry-run, which is free. Only valid queries are billed.

**The time limit.** The client accepts one to two minutes for long reports. Counting model calls and tokens does not bound time, because a rate-limited call can wait: one recorded question took 50 seconds, 27 of them waiting. So a question also has a time limit, `TURN_TIME_BUDGET_SECONDS`, 120 by default. It is a deadline. Every model call is given the time that is left as its own timeout, the retry logic stops when that time is used up, and a query gets the remaining time as its job timeout. The first version only checked the clock between steps; a review pointed out that a step in which every call hung could then run for many minutes, and the deadline closed that. The time a user takes to answer a confirmation is not counted.

**Announcing the last step.** The first recording of "Why did our churn rate spike last month?" ran eight queries, one per step, reached the limit on model calls and showed the limit message. The cost was bounded, but the work was thrown away. Now, when one model call is left, every tool result carries an instruction to answer from what has been found and to say what could not be checked. Recorded again, the same question ends in an answer on its eighth call.

**Observed.** In the recorded sessions one of 22 queries failed, on a date function that BigQuery does not support for timestamps. It was corrected on the next attempt and was not billed. Earlier runs showed the same error more often, a wrong alias, and wrong apostrophe escaping, all corrected the same way.

**Where.** `Toolbox.run_sql` in `agent/tools.py`. Tests: the "self-correction and its limits" group in `tests/test_agent.py`.

### D-22. Models are an ordered list; a rate-limited model is rested

**Situation.** The first design was "a primary model and a fallback". Real runs on the free tier changed it. The newest model allows 5 requests a minute and 20 a day. Once it was exhausted, every call still tried it first, got a rate-limit error, and only then fell back, which wasted a call each time and kept the limit from clearing.

**Decision.**

- Models are configured as an ordered list (`GEMINI_MODELS`). The first one that is available answers.
- Timeouts and server errors are retried with exponential backoff and jitter.
- A rate-limit error carries the wait the provider asks for. A short wait (up to 5 seconds) is waited out. A longer one puts that model to rest for exactly that long, and it is skipped until then.
- If every model is resting, the agent waits for the soonest one, up to a minute, and the interface says so. Beyond that it tells the user how long to wait.

**Why.** This keeps the assistant usable through the failure a reviewer on the free tier is most likely to meet, and it is also the right behaviour in production: it is a circuit breaker whose timing comes from the provider instead of a guess. A general-purpose circuit breaker shared between instances is described in the design and not built, because a single-user CLI has nothing to share.

**A second chance for a rate-limited model.** When no model answered, the rate-limited one that is due back first gets one more try: at once if its rest ended while the others were being tried, or after a wait of up to a minute. A model that simply failed is not waited for, because it has no time to come back at.

**Observed.** The recorded sessions were answered entirely by the third model in the list, with no action from the user. In two answers that model hit its own per-minute limit and was waited for, for 23 and 27 seconds.

**Where.** `llm/resilient.py`. Tests: `tests/test_llm.py`, with a fake clock so no test waits.

### D-23. A delete is prepared by the model, decided by the user, permanent, and reported by the application

**Decision.** The model can call `delete_reports` with a description of what to delete. That call deletes nothing. The graph:

1. finds the matching reports that belong to this user;
2. stores their ids in the conversation state;
3. pauses in a separate step and hands the list to the interface, which says that the deletion is permanent;
4. on "yes", deletes exactly the stored ids for good; on anything else, deletes nothing;
5. writes the outcome message itself. If nothing else was asked for, the turn ends there.

**Why each part.**

- *The pause is a step of its own.* When the graph resumes, only that step runs again, and it reads the ids from the saved state. So the set that is deleted is the set that was shown, even if more matching reports appeared meanwhile.
- *The model cannot confirm.* The decision arrives through `ChatSession.confirm`, which only the interface calls. There is no tool for it, and a "yes" typed into the chat is an ordinary message that cancels the pending request and ends the turn it belonged to.
- *The request is checked, not guessed.* The arguments of `delete_reports` come from the model. A string where a list of ids is expected would be read one character at a time, so wrong types are refused and nothing is interpreted.
- *Deleting is permanent.* The first version was a soft delete with an `/undo` command. The client then said that deleted reports do not need to be recoverable, so the restore function, the command and the extra columns were removed. This also makes the confirmation mean what it says: the brief calls the action destructive, and now it is. The confirmation and the outcome both state that it cannot be undone.
- *The audit log outlives the reports.* The request, and the confirmation or the cancellation, are recorded with the report ids, and the titles of what was requested and of what was deleted are kept. Report ids are never reused, so an id in the log cannot come to mean a different report.
- *A delete asked for together with something else.* If the model asks for a query and a delete in the same step, the query runs, the user is asked, and after the decision the model answers the rest. The outcome of the delete is kept by the application and put in front of that answer, so it is shown whatever the model says, and even if the model call fails. A second delete request in the same question is refused without asking the user again. The first version ended the turn at the confirmation and never used the query's result.
- *The application reports the outcome.* The first version returned the result to the model and let it write the reply. In a recorded run the delete succeeded, the following model call hit a rate limit, and the user was told to "try again" with no word on whether anything had been deleted. The outcome of a confirmed action is now a fixed message from code. It is also faster and costs no model call.

**Where.** `tools` and `confirm_delete` in `agent/graph.py`; `reports/store.py`. Tests: `tests/test_delete_flow.py`, `tests/test_reports.py`, and the prompt wording in `tests/test_cli.py`.

### D-24. Earlier result tables are not resent to the model

**Decision.** For each model call, the context is: the earlier questions and final answers (the last 20 messages), plus everything from the current question, including its tool calls and results. Tool calls and result tables from earlier questions are left out.

**Why.** Without this, every question would resend every table the conversation has produced, and the cost of a conversation would grow with its length. The answers already contain the figures that mattered, so follow-up questions still work, and the model can query again if it needs detail.

**Trade-off.** A follow-up that needs a number which was in an old table but not in the old answer costs one extra query.

**Where.** `_context` in `agent/graph.py`. Test: `test_follow_up_sees_earlier_answers_but_not_earlier_tables`.

### D-25. Date and revenue conventions are written down, and totals come from SQL

**Observed.** In recorded runs the smaller model produced a quarterly report that covered "the last 90 days" instead of the calendar quarter, counted returned items as revenue so that its figures disagreed with an earlier answer, and added up a brand's three monthly values itself and got the sum wrong.

**Decision.**

- The instructions state the conventions: revenue excludes cancelled and returned items, with one definition per answer; "last month" and "last quarter" mean complete calendar periods; the date range is always stated; the model never adds up or averages figures itself.
- The analyst example for quarterly reports returns every total (per month, per brand, and overall) from one SQL statement, so there is nothing left for the model to add up.

**Why this way.** These are business definitions, and the Golden bucket is where the brief says such knowledge lives. The totals were fixed in the trio alone; the calendar-quarter rule is in the trio and in the instructions. After the change, every figure in the recorded report matches the query result.

**What it does not fix.** A model can still misstate a number. The design adds a mechanical check that every figure in an answer appears in that turn's query results; it is not built.

**Where.** `agent/prompts.py`, `golden_bucket/quarterly_report.json`, `golden_bucket/brand_comparison.json`.

### D-26. The Golden bucket is a folder in the prototype

**Decision.** Seven sample trios live in `golden_bucket/` as JSON files. The two most similar to the question, by shared words, are added to the model's instructions.

**Why.** The client confirmed on 2026-10-04 that the bucket is theoretical, that it need not be implemented in the prototype, and that a local folder of sample trios is the right stand-in. They also said that the real bucket holds about 1,000 trios in JSON, which is the format the samples use, and asked how it scales with hundreds of users; that is answered in the design (section 3.1). Matching on words needs no service and no extra model calls, and the function it sits behind (`find_similar`) is what an embedding search would implement.

**Kept honest by tests.** Every stored SQL statement is run through the SQL gate and the local database in the offline suite, and on the real dataset in the BigQuery test group, where it must also find rows. A further test fails on a trio that names a brand or quotes an amount or a percentage. A trio that stops working fails the build.

**Where.** `golden/retrieval.py`, `golden_bucket/`. Tests: `tests/test_golden.py`.

### D-27. Traces are one JSON line per question; metrics are computed from them

**Decision.** Each question appends one JSON record to `logs/traces.jsonl` with every step: guard decision, model calls (which model answered, tokens, the analyst examples it was given), retries and waits, queries (the SQL written, the SQL run, rows, bytes, error code and message), confirmations, and the outcome. `/trace` shows the last one and `/stats` computes the metrics from the file.

**Why.** One record per question answers both operational questions: is it failing (aggregate the records), and why did this answer go wrong (read one record). Computing metrics from traces means they cannot disagree with each other, and a new metric needs no new instrumentation.

**What is not logged.** Result rows, only their count. The whole record is scrubbed for personal data before it is written. Time spent waiting for the user to confirm a delete is excluded from latency.

**Details that matter.** A model step is named after the model that answered it. A question counts as recovered only if a query succeeded after one had failed; an apology after a failed query is not a recovery. Query errors are counted by kind and failed model attempts by model, so the metrics say what is failing and not only how often. A model step also records a fingerprint of the tone that was in force. Any earlier answer of the same user can be opened with `/trace <id>`. Traces of a chat carry the conversation id, the same one that is stored with each saved report. A trace that cannot be written does not cost the answer, and a damaged line in the file costs that trace only.

**Considered instead.** A metrics database and a tracing service. Both are right for production, where the same records become OpenTelemetry spans; neither is needed to show the approach.

**Where.** `observability/tracing.py`. Tests: `tests/test_tracing.py`.

### D-28. What was deliberately left out of the prototype

The brief limits the prototype to four requirements, and the client asked for the Golden bucket only as a design. The following were considered and not built, to keep the prototype small enough to read in one sitting.

| Not built | Why not | Where it is designed |
|---|---|---|
| A separate model call to classify each question | The agent declines unrelated questions itself; a router would add a call to every question | DESIGN 2 |
| Verifying a token signature | There is no front end to issue a token; the mapping from claims to access is built and tested | DESIGN 1 |
| Undo for deleted reports | The client does not need deleted reports to be recoverable | DESIGN 3.3 |
| Embedding search over the Golden bucket | Seven trios; word matching is enough to show the mechanism | DESIGN 3.1 |
| User preference memory | Design-only requirement | DESIGN 3.4 |
| A cost cap computed in dollars | The client asked for it in the design only; the prototype limits model calls, tokens, time and bytes | DESIGN 3.5 |
| An evaluation harness that runs the real model | Design-only requirement; the deterministic layers are tested exhaustively instead | DESIGN 3.6 |
| A grounding check on figures in answers | Needs tolerance rules for rounding and derived figures to avoid false alarms | DESIGN 3.6 |
| The admin page and the automated quality gate for tone changes | Design-only requirement; the tone is a file that is read on every question, which shows the mechanism | DESIGN 3.8 |
| Database permissions and views | They cannot restrict a public dataset, which every Google Cloud account can read | DESIGN 3.2 |
| Collecting feedback on answers | Design-only requirement; the CLI has no place for it | DESIGN 3.4 |
| A general "needs confirmation" flag on tools | One destructive action did not need it. Simple tools are added through a table of handlers, without touching the graph | DESIGN 3.3 and 8 |
| Streaming answers, a web interface | The brief asks for a CLI | DESIGN 1 |
| Charts, email, Slack, web search | Named as future extensions | DESIGN 8 |
| Deployment, access control, backups, limits per user | Production concerns; a CLI on one machine has none of them | DESIGN 7 |

### D-29. BigQuery is the default data source, and has its own test group

**Situation.** The first version started on the local mock unless told otherwise, and its checks against BigQuery were run by hand and described in the documents. The client accepted the local database for tests but suggested testing on BigQuery as well.

**Decision.**

- The assistant uses BigQuery by default. The mock is an explicit offline mode: `--backend duckdb`.
- When the CLI starts it makes one free dry-run. If BigQuery is not set up, it says what is missing and names the offline option, instead of failing in the middle of a question.
- The checks against the real dataset are tests in the repository, run on request with `uv run pytest -m bigquery`: the schema in code against the live tables; every legitimate query, after the gate has rewritten it, as a dry-run for each profile; brand scope and personal data on real data; a bare table name refused; every analyst example.
- `uv run pytest` stays offline and leaves that group out, so the default run needs no credentials and no network.

**Why.** The brief is about BigQuery, so that is what a reviewer should get by default. Making the hand-run checks into tests means anyone can repeat them, and they are run again whenever the gate changes. Keeping the two groups apart keeps the fast suite fast and free.

**Cost.** The corpus checks are dry-runs, which are free. The rest scan about 70 MB in total, against 1 TB free per month. The group takes about a minute.

**A known notice.** Results are turned into DataFrames by the BigQuery client library, as in the runner supplied with the brief. The library has announced that this will be deprecated in favour of another package; the notice is filtered in the test configuration and nothing is affected today.

**Where.** `config.py`, `cli/app.py`, `tests/test_bigquery_live.py`, `pyproject.toml`.

### D-30. The tests are checked by breaking the code on purpose

**Situation.** A test suite can be large and still prove little: a test may assert something that stays true when the feature is broken, and whole paths may have no test at all. A review of the tests pointed at both. The confirmation prompt of the CLI, for one, was only ever tested with "y" and "n" typed in, and no test ran the chat loop itself.

**Decision.** `tests/mutation_check.py` copies the repository, breaks one rule in the copy, runs the offline suite, and reports whether a test failed. It does this for 101 rules, one at a time: no brand filter, personal data columns exposed, a delete carried out whatever the user answers, the interface passing on the opposite of the answer, retries that ignore the deadline, a trace that is not scrubbed, and so on. It is run on request and takes about five minutes.

**Result.** Every one of the 101 breaks makes a test fail. The first runs did not: they led to the tests that run the chat loop and its confirmation prompt end to end (`tests/test_cli_chat.py`), to the tests of the Gemini adapter against a stand-in client that returns real SDK objects (`tests/test_gemini.py`), and to a number of sharper assertions.

**Limits.** The list is hand-written, so it covers the rules somebody thought of. Each entry is tied to a line of source; when that line changes, the script says that the break could not be applied, and the entry has to be updated.

**Where.** `tests/mutation_check.py`. Run with `uv run python tests/mutation_check.py`.

## Known limits, and what production adds

These are stated so nobody has to discover them.

| Area | Limit in the prototype | Production answer |
|---|---|---|
| Identity | A sample token payload chosen with a command-line option; no signature check | The API verifies the JWT sent by the front end |
| Brand scope | Enforced by the SQL gate only | The gate remains the enforcement point, because BigQuery never sees the application's token; identity federation is an option if a second enforcement is wanted |
| Personal data | Column allow-list plus pattern scrubber. On the public dataset nothing in BigQuery can add to this | On the company's own data, access only to views without the personal data columns, so BigQuery refuses them too; and a managed inspection service in place of the patterns |
| Input guard | Rules, then the model's own instruction to decline | A managed prompt-safety service in front |
| Local engine | Some BigQuery functions do not translate, and a few behave differently (division by zero, weekday numbering) | Not relevant: production uses BigQuery. The BigQuery test group is the check that counts |
| Figures in answers | The model can misstate a number, or draw a conclusion its queries do not support; conventions and SQL totals reduce it | A mechanical check on the figures, and a second model's check on the conclusions, before an answer is shown |
| Statements that are not queries | A `DESCRIBE` or a write ends the attempts for that question at once, also when it was an honest slip | The same; it is the intended direction of failure |
| Queries per question | Not counted; bounded by the limits on model calls and time, and 1 GB each | Part of the cost cap in dollars |
| Cut-off answers | An answer that reached the model's output limit is shown as it is | Detected and retried with a higher limit |
| Finding reports by text | Case is ignored for unaccented letters only | Full-text search in PostgreSQL |
| Conversation state | In memory; ends with the process (reports and traces persist) | Checkpoints in PostgreSQL |
| Golden bucket retrieval | Shared words over seven files | An embedding index over about 1,000 trios |
| Cost cap | Limits on model calls, tokens, time and bytes | One setting in dollars, $1 per question by default, translated into those limits |
| Feedback | Not collected | Helpful or not helpful under each answer, stored with the trace id |
| Model quality on the free tier | After 20 requests a day per larger model, the lite model answers | Paid capacity; the first model answers everything |

## Verification against the real dataset

Run on 2026-10-04 with the author's own Google Cloud project. The first six points are now tests (`uv run pytest -m bigquery`, 79 tests).

- The schema in code matches the live tables (D-02).
- All 24 legitimate test queries, after rewriting for each of the three profiles, pass a BigQuery dry-run: 72 of 72. Dry-runs are free.
- A bare table name sent straight to BigQuery is refused (D-10).
- With real brands, each restricted profile sees only its own brands, including a brand name containing an apostrophe, which confirms values are escaped correctly.
- `SELECT * FROM users` on real data returns only the eight safe columns.
- All seven analyst examples pass the SQL gate, run on BigQuery and find rows, scanning 3 to 10 MB each.
- Three conversations were recorded with real Gemini and real BigQuery ([EXAMPLE_RUN.md](EXAMPLE_RUN.md)): 17 questions, 15 answered and 2 stopped by the guard as intended, none failed; one of 22 queries failed and was corrected by the agent; a delete was declined and another confirmed.
- Every figure in the three recorded sessions was checked against the results of its queries by running the stored SQL again. They match, apart from two percentages that the model cut off instead of rounding. The sentences around the figures that are wrong are listed at the top of the example run.
