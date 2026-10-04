# Live tracker

Updated in the same commit as the work it describes. Gate definitions are in [PLAN.md](PLAN.md#6-phases-and-exit-gates); the reasoning behind each decision is in [DECISIONS.md](DECISIONS.md).

**Legend:** ⬜ not started · 🟦 in progress · ✅ done (all gates met) · ⛔ blocked

**Last updated:** 2026-10-04 · **Current phase:** 3 · **Overall:** 3 / 10 phases

## Phase status

| # | Phase | Status | Gates met | Evidence / notes |
|---|---|:-:|:-:|---|
| 0 | Scaffold | ✅ | 5 / 5 | `uv sync`, `uv run pytest` (1 passed), `ruff check` and `ruff format --check` clean, `.env` ignored (`git check-ignore`), layout per plan |
| 1 | Mock data and data layer | ✅ | 5 / 5 | 22 tests pass: deterministic generator, schema match, referential integrity, planted patterns (`test_mock_data.py`); shared backend behaviour and BigQuery dry-run/byte-cap/error classification with stubbed client (`test_backends.py`) |
| 2 | Safety layer | ✅ | 7 / 7 | 679 tests pass. `test_sql_gate.py`: 73 hostile queries rejected and 24 legitimate queries accepted and executed, for each of 4 profiles; every PII column blocked through 4 access paths; row limit and truncation flag. `test_scoping.py`: rows per user equal pandas ground truth; 18 escape attempts return nothing out of scope; no PII value reaches a result. `test_scrubber.py`, `test_guard.py` (34 blocked, 23 realistic questions allowed). Also checked on real BigQuery: 96 of 96 dry-runs pass |
| 3 | LLM layer and resilience | 🟦 | 0 / 4 | |
| 4 | Agent graph | ⬜ | 0 / 7 | Two gates added on 2026-10-04: semantic off-topic handling, scrubbing of final answers |
| 5 | Reports and delete flow | ⬜ | 0 / 7 | |
| 6 | Observability | ⬜ | 0 / 4 | |
| 7 | CLI polish and example run | ⬜ | 0 / 4 | |
| 8 | Design document | ⬜ | 0 / 6 | |
| 9 | GCP validation | ⬜ | 0 / 5 | |

## Deliverables

| ID | Deliverable | Status |
|---|---|:-:|
| D1 | Architecture diagram | ⬜ |
| D2 | Technical explanation | ⬜ |
| D3 | Working prototype (safety, oversight, resilience, observability) | ⬜ |
| D4 | CLI chat interface | ⬜ |
| D5 | Runnable on another machine | ⬜ |
| D6 | Framework rationale and experience statement | ⬜ |

## Open questions for the client

The full wording, with the assumption used for each, is in [QUESTIONS.md](QUESTIONS.md). None of them blocks the work: each has an assumption the project is built on until an answer arrives.

**Sent to the client:** not yet.

| # | Topic | Working assumption | Answer |
|---|---|---|---|
| 1 | Which products a user may analyse | A list of brands and/or departments per user | |
| 2 | What counts as personal data | Names, email, address, postal code and coordinates are blocked; customers shown by ID; demographics allowed per customer | |
| 3 | Saved Reports library | Built here; text match on title and content; soft delete; own reports only | |
| 4 | Confirmation before deleting | One explicit confirmation listing exactly what will be deleted | |
| 5 | Golden Knowledge bucket | One JSON document per trio; hundreds to thousands; analyst approves additions | |
| 6 | Local test data | Tests use a local mock; real runs use BigQuery | |
| 7 | Identity and permissions | Single sign-on and a central permissions service | |
| 8 | Scale and response time | Tens to hundreds of users; 10 to 30 seconds per analysis | |
| 9 | Channels and integrations | Web chat over an API; email as a replaceable tool | |
| 10 | Who changes the assistant's tone | Named non-developers, versioned changes, rollback | |
| 11 | Data residency and compliance | Single region (US); Vertex AI in production | |
| 12 | Cost limits | A cap per question on model usage and data scanned | |

## Log

| Date | Change |
|---|---|
| 2026-10-01 | Repository initialised, remote set, plan and tracker written |
| 2026-10-04 | Phase 0 complete: scaffold, tooling, config, env template |
| 2026-10-04 | Phase 1 complete: mock data generator, DuckDB and BigQuery backends |
| 2026-10-04 | Canonical schema verified against the live dataset: identical apart from the deliberately omitted `user_geom` |
| 2026-10-04 | Tables now resolve only by fully qualified name on both backends, after a `WITH`-name bypass was found and closed (DECISIONS D-10) |
| 2026-10-04 | Phase 2 complete: SQL gate, per-user scoping, query gateway, PII scrubber, input guard |
| 2026-10-04 | Gate output and scoping verified on real BigQuery (dry-runs and a few small queries); separate profile file added for real brands |
| 2026-10-04 | Decision log added (`docs/DECISIONS.md`) |
| 2026-10-04 | Client questions written up with working assumptions (`docs/QUESTIONS.md`) |
