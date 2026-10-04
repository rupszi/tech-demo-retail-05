# Delivery plan

Project: data analysis chat agent for a retail company's non-technical executives (client: OpsFleet).
Last updated: 2026-10-04. Progress is tracked in [TRACKER.md](TRACKER.md); the reasoning behind each decision is in [DECISIONS.md](DECISIONS.md).

## 1. Understanding of the brief

### What is being asked

Two artifacts, weighted towards the first:

1. **Production-grade design**: HLD with a Mermaid diagram, plus a detailed technical explanation of how the system works in production (services, communication between components, where data is stored and handled).
2. **Working CLI prototype**: a chat agent over BigQuery `thelook_ecommerce` (`orders`, `order_items`, `products`, `users`) using a Gemini model.

Assessment focus: system design, the technical explanation, and an elegant prototype that fits the prototype requirements.

### Prototype scope vs design-only scope

| # | Requirement | Prototype | Design doc |
|---|---|:-:|:-:|
| 1 | Hybrid intelligence (Golden Bucket) | sample trios in a local folder (confirmed by the client) | yes |
| 2 | Safety and PII masking, per-user product scope | **yes** | yes |
| 3 | High-stakes oversight (destructive ops) | **yes** | yes |
| 4 | Continuous improvement (user and system loops) | no | yes |
| 5 | Resilience and graceful error handling | **yes** | yes |
| 6 | Quality assurance | 753 automated tests | yes |
| 7 | Observability | **yes** | yes |
| 8 | Agility (persona management) | tone file read on every question | yes |

### Points that need deliberate handling

- **PII lives in `users`**: names, email, street address, coordinates. "Top customers" must still work, so customers are shown by pseudonymous `user_id`.
- **"Each user only analyses products related to him"** is an authorization rule. It is enforced in code on the SQL, never by prompt instructions.
- **Destructive actions** (deleting saved reports) cannot be confirmed by the model. Confirmation is a deterministic step outside the model's control.
- **Resilience without inflating cost**: self-correction is bounded, dry-runs catch SQL errors before they cost anything, and every turn has a budget.
- **The Golden Bucket is theoretical**: the prototype uses a small local folder of example trios standing in for the bucket.

## 2. Assumptions (to confirm with the client)

The questions sent to the client, with the full assumption for each, are in [QUESTIONS.md](QUESTIONS.md). The table below is the summary. None of these blocks the work.

| Topic | Assumption used |
|---|---|
| Identity | SSO/OIDC at the gateway in production; the prototype uses mock user profiles (`--user`) |
| Scale | Tens to hundreds of executives, low request rate |
| Saved Reports store | Does not exist yet, so we build one (SQLite locally, managed database in production) |
| "Products related to him" | Each user has an allowed set of brands and/or departments |
| PII display | Pseudonymous `user_id` and aggregated demographics may be shown; names, emails, addresses, coordinates never |
| Report deletion | One explicit confirmation listing exactly what will be deleted; soft delete with undo; own reports only |
| Golden Bucket | One JSON document per trio; analyst approval before anything is added; a few local sample trios in the prototype |
| Email | Design only, behind a tool interface; provider not chosen |
| Data residency | Single region matching the public dataset (`US`) |

## 3. Deliverables

| ID | Deliverable | Location | Phase |
|---|---|---|---|
| D1 | Architecture diagram (Mermaid) with service choices explained | `docs/DESIGN.md` | 8 |
| D2 | Technical explanation: service/model/framework reasoning, data flow, error handling and fallbacks, setup and example run, per-requirement solution | `docs/DESIGN.md`, `README.md` | 7, 8 |
| D3 | Working prototype covering safety, high-stakes oversight, resilience, observability | `src/` | 1-6 |
| D4 | CLI chat interface | `src/` | 7 |
| D5 | Runnable on another machine, with setup instructions | `README.md` | 7, 9 |
| D6 | Framework choice with rationale and honest experience level | `docs/DESIGN.md` | 8 |

## 4. Approach

This section describes what was built. Where it differs from the first plan, section 7 says why.

- **Framework**: LangGraph, because its interrupt and checkpoint model fits the confirmation flow and gives step-level tracing. **Model**: Gemini through the `google-genai` SDK, configured as an ordered list of models; the first one that is available answers.
- **Flow**: `guard -> agent <-> tools`, plus a `confirm_delete` step. One agent loop with five tools handles questions about the data's structure, single and multi-step analysis, reports and report management.
- **Data backends**: one `DataBackend` interface with a DuckDB implementation (mock data mirroring the four tables) and a BigQuery implementation. Everything is developed and tested offline; the same code runs against BigQuery. Tables resolve only by fully qualified name on both.
- **Safety**: SQL parsed with `sqlglot`; a single query only, table and column allow-lists, a forced `LIMIT`, per-user scoping applied to the parsed query, and an output scrubber as a second layer. The agent reaches data only through a `QueryGateway` that applies all of it.
- **Delete flow**: find the user's own matching reports, store their ids in the conversation state, pause for the user's answer, soft-delete exactly those ids, report the outcome from code. Undo and an audit log.
- **Resilience**: bounded self-correction with errors classified by whether a retry can help; retries with backoff and jitter; a rate-limited model is rested and the next one answers; a budget per question; nothing crashes the interface.
- **Observability**: one structured trace per question (JSONL), with the metrics computed from the same file; `/trace` and `/stats`.
- **Golden bucket**: a local folder of analyst examples, retrieved by similarity to the question and added to the model's instructions.
- **Extensibility**: a new capability is a tool; a new data source is a `DataBackend` with a policy; a new channel calls `ChatSession`.

### Layout

```
src/retail_agent/
  config.py          settings from the environment
  agent/             conversation graph, tools, instructions, session
  safety/            policy, profiles, SQL gate, scoping, gateway, PII scrubber, input guard
  data/              DataBackend interface, DuckDB and BigQuery, schema, mock data
  llm/               model interface, Gemini adapter, retry and fallback
  reports/           saved reports with soft delete, undo and audit log
  observability/     traces and metrics
  golden/            retrieval of analyst examples
  cli/               chat interface
config/              user profiles (one file per data backend) and the tone file
golden_bucket/       sample analyst examples
tests/
docs/
```

## 5. Working rules

- Small commits at the end of each step; the tracker is updated in the same commit.
- Every decision that shapes the design is recorded in the decision log, with its reasons and the alternatives considered.
- No secrets in the repository. Keys live in a local `.env` that is git-ignored; `.env.example` documents the variables.
- Python is pinned through `uv` (3.12) rather than the system interpreter.

## 6. Phases and exit gates

A phase is complete only when every gate in it is met. Gates are checked, not assumed, and the evidence (command or test name) is recorded in the tracker.

### Phase 0: Scaffold
Project metadata, dependencies, `.env.example`, folder layout, test and lint tooling.
- [x] `uv sync` succeeds from a clean checkout
- [x] `uv run pytest` runs (a smoke test passes)
- [x] Lint and format check pass
- [x] `.env` is ignored; `.env.example` lists every variable
- [x] Folder layout matches section 4

### Phase 1: Mock data and data layer
- [x] Generator produces deterministic DuckDB data for `users`, `products`, `orders`, `order_items`
- [x] Column names and types match the real dataset (including the PII columns)
- [x] `DataBackend` interface implemented by DuckDB and BigQuery
- [x] One shared contract test suite passes against DuckDB
- [x] BigQuery backend supports dry-run and a bytes-billed cap, covered by tests with a stubbed client

### Phase 2: Safety layer
- [x] Validator rejects DML, DDL, multi-statement input, comment tricks, unlisted tables and metadata tables
- [x] PII columns cannot be reached: named references are rejected (alias, subquery, CTE), and `SELECT *` or whole-row expressions only ever see the safe columns
- [x] `LIMIT` is forced when absent or too large
- [x] Per-user scoping verified on mock data: user A never receives user B's product rows
- [x] Output scrubber masks emails, phone numbers and street addresses in free text
- [x] Rule-based input guard blocks prompt injection, requests for personal data, secret probing and obviously unrelated requests, without blocking realistic questions (semantic off-topic detection is a phase 4 gate)
- [x] Adversarial test suite: 100% blocked, with no false rejections on the valid-query suite

### Phase 3: LLM layer and resilience
- [x] Gemini client and a fake LLM share one interface
- [x] Retry with backoff and jitter, fallback through an ordered list of models, and resting a rate-limited model, each covered by tests
- [x] Per-turn budget stops runaway loops with a user-friendly message
- [x] Tests make no network calls

### Phase 4: Agent graph
- [x] Scenarios pass with a scripted model: a question about the data's structure, a single-query answer, a multi-step question, a saved report
- [x] A failing query is corrected at most twice, then the agent stops and explains
- [x] Empty result is diagnosed and explained rather than returned silently
- [x] Every capability in the brief run against real Gemini and BigQuery, and recorded in `docs/EXAMPLE_RUN.md`
- [x] Analyst examples similar to the question are added to the model's instructions (local folder)
- [x] The agent declines unrelated requests that the rule-based guard cannot recognise, without running a query (checked with real Gemini)
- [x] Final answers and saved reports pass through the PII scrubber

### Phase 5: Reports and delete flow
- [x] Reports can be saved, listed, searched and opened
- [x] "Delete reports mentioning X" and "delete all reports from this conversation" both go through confirmation
- [x] Nothing is deleted before explicit confirmation; declining changes nothing
- [x] The confirmed ID set is exactly the previewed ID set
- [x] A user cannot delete another user's reports
- [x] Confirmation is not reachable by any tool the model can call
- [x] Undo restores soft-deleted reports; every step lands in the audit log

### Phase 6: Observability
- [x] Every turn produces a trace with trace ID, per-step latency, tokens, SQL, retries and guard hits
- [x] `/trace` shows the last turn; `/stats` shows aggregate metrics
- [x] Injected failures (bad SQL, LLM timeout) are visible in the trace with their cause
- [x] Metrics defined: share answered, blocked, gave up and failed; query error rate and recovery rate; empty-result rate; latency p50/p95; tokens and model calls per question; model retries; guard blocks; redactions; confirmation outcomes

### Phase 7: CLI polish and example run
- [x] Chat loop with a `--user` option, readable tables and report rendering
- [x] No stack trace can reach the user
- [x] Example session transcript committed
- [x] README setup instructions work from a fresh clone on a clean directory

### Phase 8: Design document
- [x] Mermaid architecture diagram renders
- [x] Every service, model and framework choice has a stated reason
- [x] Data flow, error handling and fallbacks documented
- [x] A section for each of the 8 requirements, with the prototype/design split stated
- [x] Assumptions and client questions listed
- [x] Framework rationale and honest experience level included

### Phase 9: GCP validation
- [x] Same scenarios pass against real BigQuery and real Gemini
- [x] Query cost stays inside the free tier
- [x] Differences between mock and real data are fixed or documented (separate user profiles for real brands; see DECISIONS.md)
- [x] README verified from a fresh clone, with `uv` and with plain `pip`
- [x] Final tracker review: every deliverable ticked

## 7. Changes to the plan during delivery

The plan was written before any code. These are the places where delivery departed from it, and why. Each is explained in [DECISIONS.md](DECISIONS.md).

| Planned | Delivered | Why |
|---|---|---|
| A router step, then separate steps for structure questions, analysis and reports | One agent loop with tools | Multi-step questions need a variable number of queries; a router would add a model call to every question (D-20) |
| A fast model for SQL and a stronger one for reports | An ordered list of models | On the free tier the larger models allow 20 requests a day; a list keeps the assistant working (D-22) |
| A circuit breaker | A rate-limited model is rested for as long as the provider asks | Same purpose, with the timing given by the provider; a shared breaker has nothing to share in a single-user CLI (D-22) |
| The model writes the reply after a confirmed delete | The application writes it | A real run showed the reply could be lost to a rate limit after the delete had happened (D-23) |
| A metrics store next to the traces | Metrics computed from the trace file | One source, no disagreement between the two (D-27) |
| BigQuery used only in the last phase | Used from phase 2 onwards | Access was available early; free dry-runs caught problems sooner |
| Golden bucket as an optional extra | Seven sample trios, used on every question | The client confirmed the folder approach; the trios turned out to be the right place to fix real report errors (D-25, D-26) |

## 8. Definition of done

- All phase gates are met and recorded in the tracker.
- Every requirement in the brief has either working code and tests, or a design section, as stated in section 1.
- A new machine can run the prototype from the README alone.

## 9. Revision after the client's answers

The client answered all twelve questions on 2026-10-04 ([QUESTIONS.md](QUESTIONS.md)). Most answers confirm what was built. This section plans the changes that follow from the rest. Phases 10 to 17 continue the numbering above and have the same rule: a phase is complete only when every gate is met, with evidence in the tracker.

### What the answers change

| # | Answer | Effect |
|---|---|---|
| 1 | Each user sees only their brands; the CEO sees all | Scope is by brand only. Scoping by department was never asked for and is removed |
| 2 | The assumed list of personal data; customer IDs are fine; demographics per individual are fine | Confirmed. An open point in the documents is closed |
| 3 | The report store is ours to design; any term; no need to recover deleted reports; no sharing | Soft delete and undo are removed. Deleting is permanent |
| 4 | One confirmation is enough | Confirmed |
| 5 | JSON; about 1,000 trios; approval is our call; think about hundreds of users | Design: how the bucket scales |
| 6 | A local mock is fine, but test on BigQuery too | A BigQuery test group is added to the repository |
| 7 | The front end sends a JWT with the user's scopes | Profiles are built from token claims. Design: no sign-in proxy or permissions service |
| 8 | Assumptions are good; long reports may take one to two minutes | A time limit per question; no background job for reports |
| 9 | Web chat over an API; Slack outputs maybe later | Design: Slack is an output, not a second chat channel |
| 10 | One non-developer editor; an automated quality gate | Design: publishing a tone change is decided by an automated gate |
| 11 | No compliance requirements | Design: residency language removed |
| 12 | A configurable cap; assume $1 per question; design only | Design: the cap is expressed in dollars |

### Scope decisions for the revision

| Question | Decision |
|---|---|
| Remove undo and soft delete? | Yes, remove |
| Remove department scoping and the `dan` user? | Yes |
| Make BigQuery the default backend, with the mock as an explicit offline mode? | Yes |
| Add a two-minute time limit per question? | Yes |

### From what to what

| Item | From | To |
|---|---|---|
| Scope model | Brands and/or departments; four demo users | Brand only; three demo users (two with brands, the CEO) |
| "Sees everything" | A missing brand list means no restriction | An explicit grant; a user with no brand scope sees nothing |
| Where the scope comes from | A profile looked up by `--user` | A profile built from token claims (`sub`, `name`, `scopes`); the config files are sample token payloads that `--user` picks from |
| Deleting reports | Soft delete, `/undo`, restore | Permanent delete after one confirmation that says so; audit log keeps who, when and which titles |
| Default data source | Local mock | BigQuery; the mock is `--backend duckdb` |
| BigQuery in tests | Checked by hand | An opt-in test group in the repository |
| Limits per question | Model calls and tokens | Model calls, tokens and time (120 seconds by default) |
| Sign-in (design) | Identity-Aware Proxy, single sign-on, permissions service | The front end sends a signed JWT; the API verifies it and reads the scopes |
| Second enforcement in BigQuery (design) | Queries run as the signed-in user with row policies | BigQuery enforces read-only access and views without personal data; brand scope is enforced by the SQL gate |
| Golden bucket (design) | Hundreds to a few thousand trios, tagged by product scope | About 1,000 JSON trios written without brand names or figures; automated triage of candidates from many users |
| Tone changes (design) | A named group, preview, checks, a quick evaluation run | One editor; publishing only through an automated quality gate; automatic rollback |
| Cost (design) | Limits on tokens, calls and bytes | A configurable cap in dollars per question, $1 by default, translated into those limits |
| Latency (design) | 10 to 30 seconds; long reports as a background job | Seconds for questions; one to two minutes for long reports, with progress shown |
| Slack (design) | A possible second chat channel | A later output, modelled as a tool |
| Compliance (design) | Single region, data-terms caveats | No compliance requirements |

### Phase 10: Record the answers and the plan
- [x] All twelve answers recorded in `QUESTIONS.md` with the date and what each one changes
- [x] This section and the tracker cover phases 10 to 17

### Phase 11: Scope by brand, from token claims
Code: `safety/profiles.py`, `safety/scoping.py`, `config/users.*.json`, `agent/prompts.py`, `cli/app.py`.
- [x] Brand is the only scope: no department scope in code, configuration or tests
- [x] "All brands" is an explicit grant; a profile with no brand scope gets zero rows from every table
- [x] A profile is built from token claims; a token without a subject or with malformed scopes is rejected
- [x] The hostile and valid query corpora pass for every profile, and the scoping tests still match ground truth

### Phase 12: Permanent delete
Code: `reports/store.py`, `agent/graph.py`, `agent/session.py`, `agent/prompts.py`, `cli/app.py`.
- [x] No soft delete, restore or `/undo` in code, help text, instructions or tests
- [x] A confirmed delete removes the reports: they cannot be listed, opened or found afterwards, and their ids are not reused
- [x] The confirmation and the outcome both say the deletion is permanent
- [x] The audit log records the request, and the confirmation or cancellation, with the ids; the titles of what was requested and of what was deleted are kept
- [x] The earlier guarantees hold: nothing before confirmation, exactly the previewed ids, own reports only, the model cannot confirm

### Phase 13: BigQuery by default, and a BigQuery test group
Code: `config.py`, `cli/app.py`, `.env.example`, `pyproject.toml`, `tests/test_bigquery_live.py`.
- [ ] `DATA_BACKEND` defaults to `bigquery`; `--backend duckdb` runs offline on the mock
- [ ] A missing BigQuery setup gives a clear message that names the offline option
- [ ] `uv run pytest` stays offline and leaves the BigQuery group out; `uv run pytest -m bigquery` runs it
- [ ] The BigQuery group passes: schema match, the valid corpus as dry-runs for every profile, scoping and personal data on real data, a bare table name refused, every analyst example runs

### Phase 14: Time limit per question
Code: `config.py`, `agent/graph.py`.
- [ ] `TURN_TIME_BUDGET_SECONDS` (default 120) stops a question between steps with a clear message
- [ ] The stop is recorded in the trace, and a test covers it

### Phase 15: Documentation in step with the code
- [ ] `DESIGN.md` reflects every row of "From what to what"
- [ ] `DECISIONS.md`: changed entries revised, new entries added, known limits and verification current
- [ ] `README.md`: users, commands, defaults and tests match the code
- [ ] Every Mermaid diagram parses and renders
- [ ] Every relative link and anchor resolves
- [ ] No stale terms remain: undo, soft delete, department scope, the fourth user, the sign-in proxy, the permissions service, old test counts

### Phase 16: Example run and fresh clone
- [ ] Both example sessions re-recorded with the final code against BigQuery
- [ ] The figures in the recorded report checked against the query result
- [ ] Setup verified from a fresh clone, with `uv` and with `pip`

### Phase 17: Independent audit
Five separate review passes over the finished repository, each with its own focus: security of the SQL gate and scoping; documentation against code; coverage of the brief and of the client's answers; a mechanical sweep for stale text, links, counts and settings; and correctness of the agent, the delete flow and the failure handling.
- [ ] All five passes completed, each reporting findings with file and line
- [ ] Every finding checked, then fixed or recorded with the reason it stands
- [ ] Full verification repeated after the fixes: tests, lint, BigQuery group, links
- [ ] Audit summary recorded in the tracker

### Tests for this revision

| Phase | Added | Changed | Removed |
|---|---|---|---|
| 11 | `test_profiles.py`: brand scopes parsed; the all-brands grant; no scopes means no access; other scope kinds ignored; duplicates; missing subject and malformed scopes rejected | `test_scoping.py`, `test_sql_gate.py` (three profiles), `test_cli.py` | Department scope tests |
| 12 | Deleted reports are gone for good; ids are not reused; the outcome says permanent | `test_reports.py`, `test_delete_flow.py` (audit trail, messages) | Undo tests |
| 13 | `test_bigquery_live.py` (opt-in); default backend; startup message | `conftest.py`, `test_smoke.py` | |
| 14 | A question that exceeds the time limit stops with a message and a trace event | | |

