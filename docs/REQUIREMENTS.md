# What the brief asks, and how it is met

This page goes through the brief item by item: the eight requirements, the deliverables, the expected capabilities and the wider asks. For each one it says what was done and where to see it.

The statuses used:

- **Built**: in the prototype, with tests.
- **Design**: described in the design document. The brief asks for these as design only.
- **Done**: a deliverable or an ask that is complete.
- **Partly**: done, with a gap that is stated in the same row.

Where a status needs a word more ("Built, with a stated limit"), the row says what it is.

Section numbers refer to [DESIGN.md](DESIGN.md); decisions (D-08 and so on) to [DECISIONS.md](DECISIONS.md); sessions and exchanges to the recorded [example run](EXAMPLE_RUN.md). Code paths are under `src/retail_agent/`.

## The eight requirements

| # | The brief asks | Status | How it is met | Where to see it |
|---|---|---|---|---|
| 1 | **Hybrid intelligence.** Use the Golden bucket to apply the analysts' logic to new questions. Explain how the bucket is updated and how relevant data is provided at query time | Design, with a working stand-in | The trios closest to the question are put into the model's instructions as worked examples, so a definition such as "churn" comes from the analysts and not from the model. In production: embeddings and a vector index over about 1,000 JSON trios, additions approved by analysts, a nightly check that every stored query still runs, and automatic triage of candidates coming from hundreds of users. In the prototype: seven sample trios in a folder, matched by shared words, which is the stand-in agreed with the client | [3.1](DESIGN.md#31-hybrid-intelligence-the-golden-knowledge-bucket); `golden_bucket/`, `golden/retrieval.py`; `tests/test_golden.py`; session 3, exchange 2 |
| 2 | **Safety: only analysis questions, safeguarded against malicious users** | Built, with a stated limit | SQL written by the model is treated as untrusted input. It is parsed and must pass every check: one read-only query, four tables, no functions that reach outside, no parameters. Only SQL regenerated from the checked syntax tree is executed. A rule-based guard stops obvious injection, requests for personal data and off-topic requests before any model call, and the model is instructed to decline the rest. **Limit:** staying on the subject rests on those rules and on the instruction, so a determined user can get an off-topic answer. What they cannot get is data | [3.2](DESIGN.md#32-safety-and-pii), D-06, D-10, D-14; `safety/validator.py`, `safety/guard.py`; `tests/test_sql_gate.py` (80 hostile queries, each for three users), `tests/test_guard.py`; session 2, exchanges 2 to 4 |
| 2 | **Safety: no PII in the output** | Built | The seven personal data columns cannot be selected: the `users` table only ever appears as a subquery that lists the safe columns by name. Customers are shown by ID. As a second layer, a scrubber masks anything that looks like personal data in results, answers, saved reports and logs | [3.2](DESIGN.md#32-safety-and-pii), D-09, D-13; `safety/scoping.py`, `safety/scrubber.py`; `tests/test_sql_gate.py`, `tests/test_scoping.py`, `tests/test_scrubber.py`; session 1, exchanges 5 and 6 |
| 2 | **Safety: each user analyses only their own products** | Built | The user's brands come from the scopes in their token. Every table reference in a query is replaced by a subquery limited to those brands, so the limit holds in joins, subqueries and unions. No brand scope means no access; the CEO has an explicit grant for all brands | [3.2](DESIGN.md#32-safety-and-pii), D-08, D-16; `safety/scoping.py`, `safety/profiles.py`; `tests/test_scoping.py` (results compared with independently computed ones), `tests/test_profiles.py`, `tests/test_bigquery_live.py`; session 2, exchanges 1 and 3 |
| 3 | **High-stakes oversight.** "Delete all reports mentioning X", "delete the reports we made in this conversation": a strict confirmation before execution, without breaking the experience; users delete only their own reports | Built | The model can only request a delete. The application finds the user's own matching reports, stores exactly those ids, pauses, and asks once, listing the titles and saying that it is permanent. Only a yes deletes, and exactly what was listed. The outcome is written by the application, not by the model, and every step is in an audit log | [3.3](DESIGN.md#33-high-stakes-oversight), D-23; `agent/graph.py` (`confirm_delete`), `reports/store.py`, `cli/app.py`; `tests/test_delete_flow.py`, `tests/test_cli_chat.py`; session 1, exchanges 8 and 9 |
| 4 | **Continuous improvement.** Remember each user's preferences and learn them over time; the system learns from past interactions | Design | A preference store per user, filled from what users say and from what a background job infers, and added to the instructions. Feedback under each answer. A weekly loop that groups failures by cause and proposes changes to trios, instructions and evaluation cases, released only if the evaluation suite still passes. The loop was run by hand while building the prototype; the table in 3.4 lists what it changed | [3.4](DESIGN.md#34-continuous-improvement) |
| 5 | **Resilience.** Detect syntax errors and empty results, self-correct before giving up, never crash the interface, do not inflate cost, survive third-party failures | Built | Every failure is classified, because the right response depends on its cause. The model sees the error and may correct the query twice; an empty result gets a hint, once. Broken SQL is caught by a free dry-run before anything is billed. A question has limits on model calls, tokens and time, and each query is capped at 1 GB. Models are an ordered list with retries, fallback, and a rest for a rate-limited model. A failing tool, an unexpected error, Ctrl-C or an unwritable log does not end the chat | [3.5](DESIGN.md#35-resilience), [5](DESIGN.md#5-error-handling-and-fallbacks), D-04, D-21, D-22; `agent/tools.py`, `llm/resilient.py`, `data/bigquery_backend.py`; `tests/test_agent.py`, `tests/test_llm.py`, `tests/test_backends.py`; session 2, exchange 5; session 3, exchange 2 |
| 6 | **Quality assurance.** How is the agent evaluated before deployment, how is it verified that reports answer the intent, how is the experience evaluated | Design, plus the tests that exist | Five kinds of checks before deployment, from ordinary tests to an evaluation run with the real model. Three signals for intent: a rubric scored by a second model, analyst review of a sample, and what users do next. Experience: measures from the traces and moderated sessions. In the repository: 767 offline tests, 79 against BigQuery, and a script that checks the tests themselves by breaking 101 rules one at a time | [3.6](DESIGN.md#36-quality-assurance), D-30; `tests/` |
| 7 | **Observability.** Know when the agent fails and why; metrics at the agent level; support for deep-dive debugging | Built | One trace per question records every step: the guard's decision, each model call with the model that answered and its tokens, the SQL as written and as executed, errors, retries, waits and confirmations. The trace id is printed under every answer. `/trace` shows the steps behind an answer, `/stats` computes the agent-level metrics from the same traces | [3.7](DESIGN.md#37-observability), D-27; `observability/tracing.py`; `tests/test_tracing.py`; `/trace` in sessions 1 and 3, `/stats` in session 3 |
| 8 | **Agility.** Non-developers change the tone of the reports without redeployment | Design, with the mechanism built | The tone is its own layer of the instructions, apart from the safety rules. In the prototype it is a file that is read on every question, so an edit takes effect with the next message and no restart. In production: an admin page, versions, an automated quality gate that decides whether a change is published, and automatic rollback | [3.8](DESIGN.md#38-agility-changing-the-tone-without-a-deployment); `config/persona.md`, `agent/prompts.py`; `test_tone_file_is_read_on_every_question` in `tests/test_agent.py` |

## Deliverables

| # | The brief asks | Status | Where |
|---|---|---|---|
| 1 | Architecture diagram with the building blocks, services, compute and flow, and a reason for each choice | Done | [1](DESIGN.md#1-architecture); Mermaid source in [diagrams/architecture.mmd](diagrams/architecture.mmd) |
| 2.1 | Reasoning for the cloud services, models and framework | Done | The building-blocks table in [1](DESIGN.md#1-architecture), and [4](DESIGN.md#4-technology-choices-and-why) |
| 2.2 | Data flow between the components | Done | [2](DESIGN.md#2-how-a-question-is-answered), [how the components talk to each other](DESIGN.md#how-the-components-talk-to-each-other), [6](DESIGN.md#6-where-data-lives) |
| 2.3 | Error handling and fallback strategies | Done | [5](DESIGN.md#5-error-handling-and-fallbacks), [3.5](DESIGN.md#35-resilience) |
| 2.4 | Setup instructions and an example run | Done | [README](../README.md), [EXAMPLE_RUN.md](EXAMPLE_RUN.md) |
| 2.5 | How each requirement is handled | Done | [3.1 to 3.8](DESIGN.md#3-the-eight-requirements), and the table above |
| 3 | A working prototype: ask questions naturally, discuss them, create a report with action items; it supports safety, oversight, resilience and observability | Done | `src/retail_agent/`; the rows marked Built above (requirements 2, 3, 5 and 7) |
| 4 | A CLI for the chat | Done | `uv run retail-agent`; `cli/app.py` |
| 5 | Runnable on another machine | Done | [README](../README.md). Checked from a fresh clone, with `uv` and with `pip` |
| 6 | A framework of choice, why it was chosen, and the level of experience with it | Done | LangGraph: [4](DESIGN.md#4-technology-choices-and-why), D-17 |

## Expected capabilities

Each of these is shown in the recorded [example run](EXAMPLE_RUN.md), against the real dataset.

| The brief asks | Where it is shown |
|---|---|
| Customer behaviour: top customers, total spend | Session 1, exchange 5 |
| Product performance: compare X and Y and say why they differ | Session 1, exchange 3 (two brands) and exchange 4 (two products) |
| Time-based metrics: monthly revenue, up-to-date revenue by product | Session 1, exchange 2; session 3, exchange 3 |
| Questions about the structure of the database | Session 1, exchange 1 |
| Multi-step queries | Session 1, exchange 3; session 3, exchanges 1 and 2 |
| "Why are users in state X underspending, and how does that compare to state Y?" | Session 3, exchange 1 |
| "Why did our churn rate spike last month?" | Session 3, exchange 2 |
| "Create a report for the quarter with insights and action items for the next" | Session 1, exchange 7 |
| Discussing an answer: a follow-up that builds on the one before | Session 1, exchange 4 |
| SQL constructed and executed dynamically on BigQuery, over the four required tables | Every session; `data/bigquery_backend.py`, `data/schema.py` |
| A newer Gemini model | `GEMINI_MODELS`, by default `gemini-3.8-flash` first. On the free tier the larger models allow 20 requests a day, so the recorded answers came from `gemini-3.5-flash-lite`, the third in the list |

## The wider asks

| The brief asks | Status | How it is met | Where |
|---|---|---|---|
| Easily extendable with new capabilities (graphs, mail, web search) | Partly | A tool that needs only its arguments is a declaration, a method and one line in a table of handlers; the conversation graph does not change: a test puts a handler into the table and the graph calls it. **Gap:** the confirmation step is written for deleting reports. A second tool that needs confirmation, such as sending mail, would need that step made general first | [8](DESIGN.md#8-extending-it); `agent/tools.py`; `test_a_new_tool_needs_no_change_to_the_graph` |
| Easily extendable with new data sources | Partly | All data access goes through one interface with two implementations, BigQuery and a local database. **Gap:** the safety rules (allowed tables, personal data columns, how a table is limited to a user's brands) are written for these four tables. A second source would need them handed to the gateway as a policy | [8](DESIGN.md#8-extending-it), D-01; `data/base.py`, `safety/policy.py` |
| Which services are used, how the components communicate, where data is stored and handled | Done | Building blocks with a reason for each, the API and the protocol on every connection, and a table of where each kind of data lives | [1](DESIGN.md#1-architecture), [6](DESIGN.md#6-where-data-lives) |
| Detailed enough to understand how the system works in production | Done | Identity and scopes, the path of a question, what fails and what happens then, and how it is released, secured, backed up and paid for | [1](DESIGN.md#1-architecture) to [7](DESIGN.md#7-running-it-in-production) |
| The client's answers to our questions | Done | All twelve are recorded with what each one changed | [QUESTIONS.md](QUESTIONS.md) |

## What is not done

Stated here so that nobody has to find it out. The full list is in [9](DESIGN.md#9-limits-of-the-prototype).

- The recorded answers come from the smallest model in the list, because of the free-tier quota. It sometimes words a conclusion more strongly than its own numbers support. Every figure in the example run was checked against the results of its queries; the sentences that are wrong are named at the top of that page. Two checks for this are designed (3.6) and not built: one that every figure follows from the query results, and one in which a second model compares each conclusion with the results.
- The token is not verified, because there is no front end to issue one. The mapping from its claims to what a user may see is built and tested.
- The parts the brief asks for as design only are design only: the real Golden bucket, the learning loops, the evaluation with the real model, the admin page and its quality gate, and the cost cap in dollars.
