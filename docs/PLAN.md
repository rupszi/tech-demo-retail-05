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
| 1 | Hybrid intelligence (Golden Bucket) | small mock (bonus) | yes |
| 2 | Safety and PII masking, per-user product scope | **yes** | yes |
| 3 | High-stakes oversight (destructive ops) | **yes** | yes |
| 4 | Continuous improvement (user and system loops) | no | yes |
| 5 | Resilience and graceful error handling | **yes** | yes |
| 6 | Quality assurance | unit tests and a small scenario set | yes |
| 7 | Observability | **yes** | yes |
| 8 | Agility (persona management) | no | yes |

### Points that need deliberate handling

- **PII lives in `users`**: names, email, street address, coordinates. "Top customers" must still work, so customers are shown by pseudonymous `user_id`.
- **"Each user only analyses products related to him"** is an authorization rule. It is enforced in code on the SQL, never by prompt instructions.
- **Destructive actions** (deleting saved reports) cannot be confirmed by the model. Confirmation is a deterministic step outside the model's control.
- **Resilience without inflating cost**: self-correction is bounded, dry-runs catch SQL errors before they cost anything, and every turn has a budget.
- **The Golden Bucket is theoretical**: the prototype uses a small local folder of example trios standing in for the bucket.

## 2. Assumptions (to confirm with the client)

| Topic | Assumption used |
|---|---|
| Identity | SSO/OIDC at the gateway in production; the prototype uses mock user profiles (`--user`) |
| Scale | Tens to hundreds of executives, low request rate |
| Saved Reports store | Does not exist yet, so we build one (SQLite locally, managed database in production) |
| "Products related to him" | Each user has an allowed set of brands and/or departments |
| PII display | Pseudonymous `user_id` and aggregated demographics may be shown; names, emails, addresses, coordinates never |
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

- **Framework**: LangGraph, because its interrupt and checkpoint model fits the confirmation flow and gives step-level tracing. **Model**: Gemini through the `google-genai` SDK, model names configurable by environment variable (a faster model for routing and SQL, a stronger one for report writing, with fallback between them).
- **Request pipeline**: input guard, intent router, then one of schema Q&A, analysis, report, or report management. Analysis flows through golden-example retrieval, SQL generation, validation, dry-run, execution, analysis, PII scrub and response.
- **Data backends**: one `DataBackend` interface with a DuckDB implementation (mock data mirroring the four tables) and a BigQuery implementation. Everything is developed and tested offline first; BigQuery is the final validation step. Tables resolve only by fully qualified name on both.
- **Safety**: SQL parsed with `sqlglot`; single `SELECT` only, table and column allowlists, forced `LIMIT`, per-user scoping applied to the parsed query, output scrubber as a second layer. The agent reaches data only through a `QueryGateway` that applies all of it.
- **Delete flow**: resolve candidates (own reports only), snapshot exact IDs into a pending action, confirm in the CLI outside the model, soft delete with undo, audit log.
- **Resilience**: retries with backoff and jitter, circuit breaker, model fallback, capped self-correction, per-turn token and tool-call budget, friendly error messages.
- **Observability**: structured trace per turn (JSONL) plus a metrics store, exposed through `/trace` and `/stats`.
- **Extensibility**: a tool registry so new capabilities (charts, email, web search) and data sources are added as single modules.

### Planned layout

```
src/retail_agent/
  config.py          settings from environment
  data/              DataBackend interface, DuckDB + BigQuery, mock data generator
  safety/            policy, profiles, sql validator, scoping, gateway, pii scrubber, input guard
  llm/               gemini client, fake llm, retry/breaker/fallback, budget
  agent/             graph, nodes, prompts
  reports/           saved reports store, pending actions, audit log
  observability/     tracing, metrics
  cli/               chat loop and rendering
  golden/            example trios and retrieval
config/              mock user profiles, one file per data backend
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
- [ ] `uv sync` succeeds from a clean checkout
- [ ] `uv run pytest` runs (a smoke test passes)
- [ ] Lint and format check pass
- [ ] `.env` is ignored; `.env.example` lists every variable
- [ ] Folder layout matches section 4

### Phase 1: Mock data and data layer
- [ ] Generator produces deterministic DuckDB data for `users`, `products`, `orders`, `order_items`
- [ ] Column names and types match the real dataset (including the PII columns)
- [ ] `DataBackend` interface implemented by DuckDB and BigQuery
- [ ] One shared contract test suite passes against DuckDB
- [ ] BigQuery backend supports dry-run and a bytes-billed cap, covered by tests with a stubbed client

### Phase 2: Safety layer
- [ ] Validator rejects DML, DDL, multi-statement input, comment tricks, unlisted tables and metadata tables
- [ ] PII columns cannot be reached: named references are rejected (alias, subquery, CTE), and `SELECT *` or whole-row expressions only ever see the safe columns
- [ ] `LIMIT` is forced when absent or too large
- [ ] Per-user scoping verified on mock data: user A never receives user B's product rows
- [ ] Output scrubber masks emails, phone numbers and street addresses in free text
- [ ] Rule-based input guard blocks prompt injection, requests for personal data, secret probing and obviously unrelated requests, without blocking realistic questions (semantic off-topic detection is a phase 4 gate)
- [ ] Adversarial test suite: 100% blocked, with no false rejections on the valid-query suite

### Phase 3: LLM layer and resilience
- [ ] Gemini client and a fake LLM share one interface
- [ ] Retry with backoff and jitter, circuit breaker and model fallback each covered by tests
- [ ] Per-turn budget stops runaway loops with a user-friendly message
- [ ] Tests make no network calls

### Phase 4: Agent graph
- [ ] Scripted scenarios pass with the fake LLM: schema question, top customers, monthly revenue, product comparison, multi-step question, report with action items
- [ ] Syntax error triggers at most N self-correction attempts, then a graceful message
- [ ] Empty result is diagnosed and explained rather than returned silently
- [ ] Each scenario also run once against real Gemini and the outcome recorded
- [ ] Golden-example retrieval feeds the SQL step (mock bucket)
- [ ] The router declines off-topic requests that the rule-based guard cannot recognise, without running a query
- [ ] Final answers and saved reports pass through the PII scrubber

### Phase 5: Reports and delete flow
- [ ] Reports can be saved, listed, searched and opened
- [ ] "Delete reports mentioning X" and "delete all reports from this conversation" both go through confirmation
- [ ] Nothing is deleted before explicit confirmation; declining changes nothing
- [ ] The confirmed ID set is exactly the previewed ID set
- [ ] A user cannot delete another user's reports
- [ ] Confirmation is not reachable by any tool the model can call
- [ ] Undo restores soft-deleted reports; every step lands in the audit log

### Phase 6: Observability
- [ ] Every turn produces a trace with trace ID, per-step latency, tokens, SQL, retries and guard hits
- [ ] `/trace` shows the last turn; `/stats` shows aggregate metrics
- [ ] Injected failures (bad SQL, LLM timeout) are visible in the trace with their cause
- [ ] Metrics defined: success rate, SQL error and retry rate, empty-result rate, latency p50/p95, tokens and cost per turn, guard blocks, confirmation outcomes

### Phase 7: CLI polish and example run
- [ ] Chat loop with user switching, readable tables and report rendering
- [ ] No stack trace can reach the user
- [ ] Example session transcript committed
- [ ] README setup instructions work from a fresh clone on a clean directory

### Phase 8: Design document
- [ ] Mermaid architecture diagram renders
- [ ] Every service, model and framework choice has a stated reason
- [ ] Data flow, error handling and fallbacks documented
- [ ] A section for each of the 8 requirements, with the prototype/design split stated
- [ ] Assumptions and client questions listed
- [ ] Framework rationale and honest experience level included

### Phase 9: GCP validation
- [ ] Same scenarios pass against real BigQuery and real Gemini
- [ ] Query cost stays inside the free tier
- [ ] Differences between mock and real data are fixed or documented (schema, gate output and scoping were already verified on real data on 2026-10-04; see DECISIONS.md)
- [ ] Final README verified from a fresh clone
- [ ] Final tracker review: every deliverable ticked

## 7. Definition of done

- All phase gates are met and recorded in the tracker.
- Every requirement in the brief has either working code and tests, or a design section, as stated in section 1.
- A new machine can run the prototype from the README alone.
