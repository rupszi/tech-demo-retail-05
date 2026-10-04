# Live tracker

Updated with the work it describes. Gate definitions are in [PLAN.md](PLAN.md#6-phases-and-exit-gates) (phases 0 to 9) and [PLAN.md, section 9](PLAN.md#9-revision-after-the-clients-answers) (phases 10 to 17). The reasoning behind each decision is in [DECISIONS.md](DECISIONS.md).

**Legend:** ⬜ not started · 🟦 in progress · ✅ done (all gates met) · ⛔ blocked

**Last updated:** 2026-10-04 · **Overall:** phases 0 to 9 delivered the prototype. Phases 10 to 17 are the revision after the client's answers.

**Tests:** 636 offline in about 3 seconds (`uv run pytest`), and 79 against BigQuery in about a minute (`uv run pytest -m bigquery`). **Lint:** clean.

## Original delivery

The evidence below is stated against the current code, so it reflects the revision where a later phase changed something.

| # | Phase | Status | Gates met | Evidence / notes |
|---|---|:-:|:-:|---|
| 0 | Scaffold | ✅ | 5 / 5 | `uv sync`, `uv run pytest`, `ruff check` and `ruff format --check` clean, `.env` ignored, layout per plan |
| 1 | Mock data and data layer | ✅ | 5 / 5 | `test_mock_data.py`: deterministic generator, schema match, referential integrity, planted patterns. `test_backends.py`: shared backend behaviour; BigQuery dry-run, byte cap and error classification with a stubbed client |
| 2 | Safety layer | ✅ | 7 / 7 | `test_sql_gate.py`: 73 hostile queries rejected and 24 legitimate queries accepted and executed, for each of 3 profiles; every PII column blocked through 4 access paths; row limit and truncation flag. `test_scoping.py`: rows per user equal pandas ground truth; 18 escape attempts return nothing out of scope; no PII value reaches a result. `test_scrubber.py`, `test_guard.py` (34 blocked, 23 realistic questions allowed) |
| 3 | LLM layer and resilience | ✅ | 4 / 4 | `test_llm.py`: backoff with jitter and a cap, fallback through the model list, short provider waits honoured, a rate-limited model rested and skipped, waiting for the soonest model, a clear error when all are limited. `test_agent.py`: limits per question. No offline test uses the network |
| 4 | Agent graph | ✅ | 7 / 7 | `test_agent.py` (25 tests) with a scripted model: structure question without a query, single and multi-step answers, scoped data, self-correction, giving up at the limit, forbidden SQL not retried, empty results, large results cut, limits on calls, tokens and time, outages, crashes, blocked messages, scrubbed answers. `test_golden.py`. Real Gemini and BigQuery: `docs/EXAMPLE_RUN.md` |
| 5 | Reports and delete flow | ✅ | 7 / 7 | `test_reports.py` (10) and `test_delete_flow.py` (16): nothing deleted before confirmation; declining changes nothing; exactly the previewed ids are deleted even when more match later; another user's reports cannot be targeted; the model cannot confirm; audit log; the outcome is reported by the application. The original undo gate was superseded by phase 12 |
| 6 | Observability | ✅ | 4 / 4 | `test_tracing.py`: one JSON line per question with steps, timing, tokens, SQL and errors; failures recorded with their cause; waiting for the user excluded from latency; metrics computed from traces. `/trace` and `/stats` shown in the example run |
| 7 | CLI and example run | ✅ | 4 / 4 | `test_cli.py` (6); two recorded sessions in `docs/EXAMPLE_RUN.md`; setup verified from a fresh clone |
| 8 | Design document | ✅ | 6 / 6 | `docs/DESIGN.md`: six Mermaid diagrams, all checked to parse and render; reasons for every service, model and framework; data flow; error handling; one section per requirement; framework rationale and experience level |
| 9 | GCP validation | ✅ | 5 / 5 | Now covered by the BigQuery test group (phase 13) and the recorded sessions (phase 16) |

## Revision after the client's answers

| # | Phase | Status | Gates met | Evidence / notes |
|---|---|:-:|:-:|---|
| 10 | Record the answers and the plan | ✅ | 2 / 2 | `QUESTIONS.md` holds all twelve answers and what each changes; `PLAN.md` section 9 has the phases, gates and tests |
| 11 | Scope by brand, from token claims | ✅ | 4 / 4 | `test_profiles.py` (19): brand scopes, the explicit all-brands grant, no scope means no access, other scope kinds ignored, a missing subject and malformed scopes rejected. `test_scoping.py`: a profile with no brand scope gets zero rows from all four tables; rows per user still equal ground truth. `test_sql_gate.py`: both corpora pass for all three profiles |
| 12 | Permanent delete | ✅ | 5 / 5 | `test_reports.py`: deleted reports cannot be listed, opened or found; ids are not reused; the audit entry keeps the titles. `test_delete_flow.py`: a confirmed delete is permanent and says so; the request is audited with titles; all earlier guarantees still pass. `test_cli.py`: the prompt names the reports and says the deletion is permanent. No restore function or `/undo` remains in `src/` |
| 13 | BigQuery by default, and a BigQuery test group | ✅ | 4 / 4 | `test_smoke.py`: BigQuery is the default. `test_cli.py`: a missing setup is reported with the offline option named, and a missing model key without it. `uv run pytest`: 636 offline tests, the BigQuery group deselected. `uv run pytest -m bigquery`: 79 tests pass against the real dataset (schema, 72 dry-runs of the valid corpus for three profiles, scoping and personal data on real data, a bare table name refused, all analyst examples) |
| 14 | Time limit per question | ✅ | 2 / 2 | `test_agent.py`: a question past 120 seconds is stopped before its next step, with a message naming the limit and a `budget: time` step in the trace; the limit is a setting |
| 15 | Documentation in step with the code | 🟦 | 0 / 6 | |
| 16 | Example run and fresh clone | 🟦 | 0 / 3 | |
| 17 | Independent audit | ⬜ | 0 / 4 | |

## Deliverables

| ID | Deliverable | Where | Status |
|---|---|---|:-:|
| D1 | Architecture diagram with service choices explained | [DESIGN.md, section 1](DESIGN.md#1-architecture) | ✅ |
| D2 | Technical explanation: choices, data flow, error handling, setup and example run, each requirement | [DESIGN.md](DESIGN.md), [README](../README.md), [EXAMPLE_RUN.md](EXAMPLE_RUN.md) | ✅ |
| D3 | Working prototype: safety, oversight, resilience, observability | `src/retail_agent/` | ✅ |
| D4 | CLI chat interface | `uv run retail-agent` | ✅ |
| D5 | Runnable on another machine | [README](../README.md), verified from a fresh clone | ✅ |
| D6 | Framework rationale and experience statement | [DESIGN.md, section 4](DESIGN.md#4-technology-choices-and-why) | ✅ |

## Questions for the client

The full wording, the assumption used for each and what each answer changed are in [QUESTIONS.md](QUESTIONS.md).

**Sent on 2026-10-04; all twelve answered the same day.**

| # | Topic | Answer | Result |
|---|---|---|---|
| 1 | Which products a user may analyse | Each user sees only their brands; the CEO sees all | Changed: brand is the only scope (phase 11) |
| 2 | What counts as personal data | The assumed list; customer IDs fine; demographics per individual fine | Confirmed |
| 3 | Saved Reports library | Ours to design; any term; no recovery needed; no sharing | Changed: deleting is permanent (phase 12) |
| 4 | Confirmation before deleting | Yes | Confirmed |
| 5 | Golden Knowledge bucket | Theoretical; JSON; about 1,000 trios; think about hundreds of users | Confirmed; design extended (phase 15) |
| 6 | Local test data | Fine, but test on BigQuery too | Changed: BigQuery test group and default (phase 13) |
| 7 | Identity and permissions | The front end sends a JWT with the user's scopes | Changed: profiles from token claims (phases 11, 15) |
| 8 | Scale and response time | Assumptions good; long reports may take 1 to 2 minutes | Confirmed; time limit added (phase 14) |
| 9 | Channels and integrations | Web chat over an API; Slack outputs maybe later | Confirmed; design wording (phase 15) |
| 10 | Who changes the assistant's tone | One non-developer; an automated quality gate | Changed in the design (phase 15) |
| 11 | Data residency and compliance | No compliance requirements | Simplifies the design (phase 15) |
| 12 | Cost limits | Configurable; $1 per question; design only | Changed in the design (phase 15) |

## Log

| Date | Change |
|---|---|
| 2026-10-01 | Repository initialised, remote set, plan and tracker written |
| 2026-10-04 | Phase 0: scaffold, tooling, settings, env template |
| 2026-10-04 | Phase 1: mock data generator, DuckDB and BigQuery backends. Schema verified against the live dataset |
| 2026-10-04 | A `WITH`-name bypass in the first SQL gate was found and closed; tables now resolve only by fully qualified name (D-10) |
| 2026-10-04 | Phase 2: SQL gate, per-user scoping, query gateway, PII scrubber, input guard. Verified on real BigQuery |
| 2026-10-04 | Decision log and client questions written; questions sent to the client |
| 2026-10-04 | Phase 3: model interface, Gemini adapter, retry and fallback |
| 2026-10-04 | Phases 4 to 7: conversation graph, tools, reports store, delete flow, traces, CLI |
| 2026-10-04 | First real runs on the free tier: rate limits led to resting rate-limited models and an ordered model list (D-22) |
| 2026-10-04 | A real run showed a delete outcome lost to a rate limit; the application now reports it (D-23) |
| 2026-10-04 | Real reports showed a wrong period and inconsistent revenue; conventions and analyst examples fixed it (D-25) |
| 2026-10-04 | Phases 8 and 9: design document, README, pinned requirements, recorded sessions; setup verified from a fresh clone |
| 2026-10-04 | The client answered all twelve questions. Revision planned as phases 10 to 17 |
| 2026-10-04 | Phase 11: brand is the only scope; profiles come from token claims; access is denied by default (D-16) |
| 2026-10-04 | Phase 12: deleting is permanent; undo removed (D-23) |
| 2026-10-04 | Phase 13: BigQuery is the default; a BigQuery test group was added (D-29) |
| 2026-10-04 | Phase 14: a question has a time limit of 120 seconds (D-21) |
