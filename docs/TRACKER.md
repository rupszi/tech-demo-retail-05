# Live tracker

Updated with the work it describes. Gate definitions are in [PLAN.md](PLAN.md#6-phases-and-exit-gates) (phases 0 to 9) and [PLAN.md, section 9](PLAN.md#9-revision-after-the-clients-answers) (phases 10 to 18). The reasoning behind each decision is in [DECISIONS.md](DECISIONS.md).

**Legend:** ⬜ not started · 🟦 in progress · ✅ done (all gates met) · ⛔ blocked

**Last updated:** 2026-10-04 · **Overall:** phases 0 to 9 delivered the prototype. Phases 10 to 16 are the revision after the client's answers. Phase 17 is an independent review of the result and the fixes that followed. Phase 18 made the repository ready for submission: the open points closed, four more reviews, and the example run recorded with the final code. Every gate is met.

**Tests:** 767 offline in about 3 seconds (`uv run pytest`), and 79 against BigQuery in about a minute (`uv run pytest -m bigquery`). **Lint:** clean.

## Original delivery

The evidence below is stated against the current code, so it reflects the revision where a later phase changed something.

| # | Phase | Status | Gates met | Evidence / notes |
|---|---|:-:|:-:|---|
| 0 | Scaffold | ✅ | 5 / 5 | `uv sync`, `uv run pytest`, `ruff check` and `ruff format --check` clean, `.env` ignored, layout per plan |
| 1 | Mock data and data layer | ✅ | 5 / 5 | `test_mock_data.py`: deterministic generator, schema match, referential integrity, planted patterns. `test_backends.py`: shared backend behaviour; BigQuery dry-run, byte cap and error classification with a stubbed client |
| 2 | Safety layer | ✅ | 7 / 7 | `test_sql_gate.py`: 80 hostile queries rejected and 24 legitimate queries accepted and executed, for each of 3 profiles; every PII column blocked through 4 access paths; row limit and truncation flag. `test_scoping.py`: rows per user equal pandas ground truth; 18 escape attempts return nothing out of scope; no PII value reaches a result. `test_scrubber.py`, `test_guard.py` (35 blocked, 24 realistic questions allowed) |
| 3 | LLM layer and resilience | ✅ | 4 / 4 | `test_llm.py`: backoff with jitter and a cap, fallback through the model list, short provider waits honoured, a rate-limited model rested and skipped, waiting for the soonest model, a clear error when all are limited. `test_agent.py`: limits per question. No offline test uses the network |
| 4 | Agent graph | ✅ | 7 / 7 | `test_agent.py` (42 tests) with a scripted model: structure question without a query, single and multi-step answers, scoped data, self-correction, giving up at the limit, forbidden SQL not retried, empty results, large results cut, limits on calls, tokens and time, outages, crashes, blocked messages, scrubbed answers. `test_golden.py`. Real Gemini and BigQuery: `docs/EXAMPLE_RUN.md` |
| 5 | Reports and delete flow | ✅ | 7 / 7 | `test_reports.py` (11) and `test_delete_flow.py` (37): nothing deleted before confirmation; declining changes nothing; exactly the previewed ids are deleted even when more match later; another user's reports cannot be targeted; the model cannot confirm; audit log; the outcome is reported by the application. The original undo gate was superseded by phase 12 |
| 6 | Observability | ✅ | 4 / 4 | `test_tracing.py`: one JSON line per question with steps, timing, tokens, SQL and errors; failures recorded with their cause; waiting for the user excluded from latency; metrics computed from traces. `/trace` and `/stats` shown in the example run |
| 7 | CLI and example run | ✅ | 4 / 4 | `test_cli.py` (7) and `test_cli_chat.py` (11), which runs the chat loop end to end; three recorded sessions in `docs/EXAMPLE_RUN.md`; setup verified from a fresh clone |
| 8 | Design document | ✅ | 6 / 6 | `docs/DESIGN.md`: six Mermaid diagrams, all checked to parse and render; reasons for every service, model and framework; data flow; error handling; one section per requirement; framework rationale and experience level |
| 9 | GCP validation | ✅ | 5 / 5 | Now covered by the BigQuery test group (phase 13) and the recorded sessions (phase 16) |

## Revision after the client's answers

| # | Phase | Status | Gates met | Evidence / notes |
|---|---|:-:|:-:|---|
| 10 | Record the answers and the plan | ✅ | 2 / 2 | `QUESTIONS.md` holds all twelve answers and what each changes; `PLAN.md` section 9 has the phases, gates and tests |
| 11 | Scope by brand, from token claims | ✅ | 4 / 4 | `test_profiles.py` (25): brand scopes, the explicit all-brands grant, no scope means no access, other scope kinds ignored, a missing subject and malformed scopes rejected. `test_scoping.py`: a profile with no brand scope gets zero rows from all four tables; rows per user still equal ground truth. `test_sql_gate.py`: both corpora pass for all three profiles |
| 12 | Permanent delete | ✅ | 5 / 5 | `test_reports.py`: deleted reports cannot be listed, opened or found; ids are not reused; the audit entry keeps the titles. `test_delete_flow.py`: a confirmed delete is permanent and says so; the request is audited with titles; all earlier guarantees still pass. `test_cli.py`: the prompt names the reports and says the deletion is permanent. No restore function or `/undo` remains in `src/` |
| 13 | BigQuery by default, and a BigQuery test group | ✅ | 4 / 4 | `test_smoke.py`: BigQuery is the default. `test_cli.py`: a missing setup is reported with the offline option named, and a missing model key without it. `uv run pytest`: 767 offline tests, the BigQuery group deselected. `uv run pytest -m bigquery`: 79 tests pass against the real dataset (schema, 72 dry-runs of the valid corpus for three profiles, scoping and personal data on real data, a bare table name refused, all analyst examples) |
| 14 | Time limit per question | ✅ | 2 / 2 | `test_agent.py`: a question past 120 seconds is stopped, with a message naming the limit and a `budget: time` step in the trace; the limit is a setting. Since phase 18 the limit is a deadline handed to every model call and query |
| 15 | Documentation in step with the code | ✅ | 6 / 6 | `DESIGN.md`, `DECISIONS.md`, `README.md`, `PLAN.md` and `QUESTIONS.md` revised. All seven Mermaid diagrams (six in the design, one in the README) parse and render. A link check over eight documents finds no broken link or anchor. A sweep for the removed features finds them only where the documents describe what changed. Settings in `config.py` and `.env.example` match exactly |
| 16 | Example run and fresh clone | ✅ | 3 / 3 | Both sessions were re-recorded against BigQuery after the revision and their report checked against its queries. That recording has since been replaced: a third session was added in phase 17, and all three were recorded again in phase 18 with the final code. From a fresh clone: `uv sync`, 767 offline tests, lint, `--list-users`, a clear message without a key, offline mode with `--backend duckdb`, and the `pip` route all work |
| 17 | Independent review | ✅ | 4 / 4 | Two review passes ran: coverage of the brief and of the client's answers (19 findings, listed below, and one more found while fixing them), and a mechanical sweep of 13 checks for stale text, links, counts and settings (no findings). Three further passes were planned and not run. All 20 findings are settled. Verified again afterwards: 767 offline tests, lint, 79 BigQuery tests, the relative links in 8 documents, all 7 diagrams, and a fresh clone with `uv` and with `pip` |
| 18 | Ready for submission | ✅ | 8 / 8 | The deadline, the trace naming and the query-with-delete case are fixed, with tests. Every module has inline comments. Four more reviews ran on the result: the safety layer, correctness and the tests, the documents against the code, and coverage of the brief; their findings are under "Second review" below. The tests now run the chat loop and its confirmation prompt end to end and exercise the Gemini adapter. `tests/mutation_check.py` breaks 101 rules on purpose, one at a time: every one makes a test fail. All three example sessions were recorded with the final code, and every figure in them was checked against the results of its queries. Verified at the end: 767 offline tests, lint, 79 BigQuery tests, the relative links in 8 documents, all 7 diagrams, and a fresh clone with `uv` and with `pip` |

## Second review

From phase 18: four independent reviews of the repository as it stood after the fixes above. One attacked the safety layer; one checked correctness and whether the tests are valid; one checked every statement in the documents against the code; one went through the brief item by item. None found a way to get personal data out, to see another user's brands, to reach a table outside the four, or to delete without the user's approval. What they did find is below. Every finding was reproduced before it was acted on.

| # | Finding | Severity | What was done | Status |
|---|---|---|---|:-:|
| S1 | A report title with terminal formatting in it crashed the chat when it was listed, and could hide or restyle what a confirmation shows | Medium | Text from users, the model and errors is escaped wherever it is printed. Test: `test_text_from_users_and_the_model_is_shown_as_written` | ✅ |
| S2 | The SQL the model wrote, error texts and the criteria of a delete request were logged unscrubbed | Medium | The whole trace is scrubbed before it is written, and so is the audit detail | ✅ |
| S3 | The arguments of a delete request were coerced: `report_ids="12"` was read as reports 1 and 2 | Low | Types are checked; a wrong type is refused. Eight cases in `test_delete_flow.py` | ✅ |
| S4 | A new message while a confirmation was pending let the rest of the old turn run on unseen | Low | The pending turn ends there. Test: `test_a_new_message_ends_the_whole_pending_turn` | ✅ |
| S5 | System variables (`@@project_id`) and query parameters passed the SQL gate | Low | Rejected; three cases added to the hostile corpus | ✅ |
| S6 | A query that timed out was run a second time, and the first job was left running | Low | The job is cancelled, and the model is told to narrow the query instead | ✅ |
| S7 | A user listed twice in a sample file silently took the last entry; `brand: *` with a space granted every brand | Low | A duplicate is an error; the grant must be written exactly | ✅ |
| S8 | A personal data column named in `USING (...)` or as a field of a row was refused by the database, not by the gate's clearer message | Low | The first check now looks at every identifier | ✅ |
| C1 | Every "top 5" result was reported to the model as cut off | Medium | Only our own row limit counts as cut off. Test: `test_a_limit_the_query_chose_itself_is_not_reported_as_cut_off` | ✅ |
| C2 | Ctrl-C while a question was being answered ended the chat with a stack trace | Medium | The question is dropped, its trace is closed, and the chat continues | ✅ |
| C3 | "Recovered after a query error" counted any answer, also an apology | Medium | A question counts as recovered only if a later query succeeded | ✅ |
| C4 | A trace file that could not be written cost the answer and ended the chat | Medium | A failed write is logged as a warning; the answer is shown | ✅ |
| C5 | Several queries in one call ended every further query for that question | Low | First judged to be by design; then a real run did exactly this and the agent gave up. Now an honest mistake with a retry, unless anything other than a query is among the statements | ✅ |
| C6 | The graph's own step ceiling (60) would be reached before a model-call limit above 28 | Low | The ceiling follows the limit | ✅ |
| C7 | A turn that crashed left a question without an answer in the history | Low | An unanswered question is left out of what the model is shown | ✅ |
| C8 | Tool results could go back to the model in a different order than the calls | Low | Results are sent in the order of the calls | ✅ |
| C9 | Waiting for a rate-limited model: the wait could end after the deadline; a model that had merely failed hid one that was due back; a rest that ended meanwhile gave no second chance | Low | All three fixed; the last one surfaced as a test that failed once in a while | ✅ |
| C10 | A malformed retry delay from the provider could raise; an unexpected SDK error skipped the fallback; refused content was sent to the same model three times | Low | Malformed delays are ignored; unexpected errors move on to the next model; refused content is not retried on the same model | ✅ |
| C11 | One damaged line made the whole trace file unreadable; a wrong value in `.env` or a broken example file ended the start with a stack trace | Low | Damaged lines are skipped; start-up problems are reported by name | ✅ |
| C12 | Traces written by the CLI all carried the same session id | Low | They carry the conversation id, the one stored with each saved report | ✅ |
| C13 | A BigQuery quota error was treated as the model's mistake | Low | It is treated as an outage | ✅ |
| C14 | The metric `llm_retries` counts failed attempts, not retries | Low | The name is kept; the documents now say what it counts | ✅ |
| C15 | A single statement that is not a query ends the attempts at once; a cut-off answer is shown as it is; the offline engine differs from BigQuery in a few functions; text search ignores case for unaccented letters only; queries per question are not counted separately | Low | Left as they are and listed in the limits of the design (section 9, and 3.5 for the queries per question) and of the decision log | ✅ |
| D1 | Documents against code: thirteen discrepancies. Most were numbers and sentences left over from earlier recordings (a 76-second answer, "no code changed", "anything else cancels", a timed-out query filed under outages); six were comments that no longer matched the code beneath them | Low | Each corrected where it stood | ✅ |
| D2 | The offline tests read the developer's own `.env`, so two of them failed when a documented setting was changed there | Medium | The offline tests no longer read it; the BigQuery group reads it on purpose | ✅ |
| D3 | The sample trios were labelled as written by a human analyst | Medium | The folder's README and every file now say that they are samples written for this prototype | ✅ |
| B1 | Against the brief: the design said little about operating the system (release, access, limits per user, backup, retention, cost), and its failure table covered two services | Medium | A new section 7 in the design, and five more rows in section 5 | ✅ |
| B2 | The example run showed no follow-up question, no comparison of two products and no question off the subject; the header named a model that answered nothing | Medium | Recorded again with those exchanges. The header lists the models in order and the line under each answer names the one that answered | ✅ |
| B3 | Extensibility was described but not shown; the metrics said how often things fail but not what; an earlier trace could not be opened | Low | A table of handlers for simple tools, with a test that adds one; query errors by kind and failed attempts by model in `/stats`; `/trace <id>` | ✅ |
| B4 | Sentences that claimed more than the code does: a user who talks the model round "has gained nothing", "nothing in a trio can leak", "look-alike letters", an empty result "then stop" | Low | Reworded to what holds; the rule for trios is now a test | ✅ |
| B5 | The model still words some conclusions more strongly than its queries support; the input guard can be passed by rephrasing; the confirmation step is written for deleting only | — | Left as they are. Each is stated in the limits of the design, and the wrong sentences of the recorded run are named at the top of it | ✅ |
| T | The tests: the chat loop and its confirmation prompt had no test; the Gemini adapter was tested through helpers only; several tests would have passed with the feature broken | — | New tests for both (`test_cli_chat.py`, `test_gemini.py`), for per-question resets, the report tools, history trimming and exact metric values; weak assertions sharpened. `tests/mutation_check.py` breaks 101 rules one at a time: every break is caught (D-30) | ✅ |

## Deliverables

| ID | Deliverable | Where | Status |
|---|---|---|:-:|
| D1 | Architecture diagram with service choices explained | [DESIGN.md, section 1](DESIGN.md#1-architecture) | ✅ |
| D2 | Technical explanation: choices, data flow, error handling, setup and example run, each requirement | [DESIGN.md](DESIGN.md), [REQUIREMENTS.md](REQUIREMENTS.md), [README](../README.md), [EXAMPLE_RUN.md](EXAMPLE_RUN.md) | ✅ |
| D3 | Working prototype: safety, oversight, resilience, observability | `src/retail_agent/` | ✅ |
| D4 | CLI chat interface | `uv run retail-agent` | ✅ |
| D5 | Runnable on another machine | [README](../README.md), verified from a fresh clone | ✅ |
| D6 | Framework rationale and experience statement | [DESIGN.md, section 4](DESIGN.md#4-technology-choices-and-why) | ✅ |

## Review findings

From phase 17. Each finding was checked against the code and the documents before anything was changed. The rule for the fixes is the smallest change that settles the finding.

| # | Finding | Severity | What was done | Status |
|---|---|---|---|:-:|
| 1 | The plan and tracker mixed working notes with the record of what was delivered, and listed a gate that was not a product gate | High | The notes are gone and the gate is dropped. Both documents now hold only what was delivered and how it was checked | ✅ |
| 2 | The design said that views in our own project keep personal data out of reach on the public dataset | High | Corrected in DESIGN 3.2 and in the decision log. Views restrict the company's own data. The public dataset is readable by every account, so on it the SQL gate is the only enforcement | ✅ |
| 3 | Extensibility was described as if a tool registry and a "needs confirmation" flag already exist | Medium | DESIGN 3.3 and 8 now separate what exists (a declaration, a method and a branch; a pause written for deletes) from what would be added (a registry, a flag, a policy handed to the gateway) | ✅ |
| 4 | Traces did not record which analyst examples were given to the model | Medium | Every model step in a trace now lists the examples it was given (`examples`). Test: `test_the_trace_names_the_analyst_examples_the_model_was_given` | ✅ |
| 5 | Loose wording in two recorded answers passed without comment | Medium | Both sentences are named at the top of `EXAMPLE_RUN.md`, with what was checked and what was not. The stored SQL was run again: the table in the second case is right and the sentence is not | ✅ |
| 6 | The example run did not show the brief's own questions on states, churn and revenue by product | Medium | Session 3, as the CEO, asks all three. Every figure in it was checked against the results of its queries | ✅ |
| 7 | No identity provider in the architecture; token expiry not described | Medium | The identity provider is in the architecture diagram. DESIGN 1 says who issues the token, how its keys are checked, and what happens when it expires during a conversation | ✅ |
| 8 | No protocols or API surface between the components | Medium | New part in DESIGN 1, "How the components talk to each other": the API calls, and the protocol on each connection | ✅ |
| 9 | Feedback from users drives three loops, but nothing in the design collected it | Medium | DESIGN 3.4 describes how feedback is collected and stored, and the API has a call for it. Not built; listed in the limits | ✅ |
| 10 | The time limit is checked between steps; how long one step can run was not stated | Medium | First stated in the design as a limit of the prototype. Fixed in phase 18: the time limit is now a deadline handed to every model call and query, and nothing new starts after it. Tests in `test_agent.py`, `test_llm.py` and `test_backends.py` | ✅ |
| 11 | The system-level learning loop had no grouping rule and no example of a proposal | Medium | DESIGN 3.4 says how failures are grouped and gives three kinds of proposal | ✅ |
| 12 | Golden bucket: where the index lives was inconsistent; the cache and "strip brands and figures" were unexplained | Low | The index is in PostgreSQL only; the cache claim is removed; DESIGN 3.1 explains the stripping | ✅ |
| 13 | An unverifiable sentence about a console name | Low | Removed | ✅ |
| 14 | A docstring mentioned a router that does not exist | Low | Corrected in `safety/guard.py`; the docstring of `agent/tools.py` now says what adding a tool takes today | ✅ |
| 15 | The input guard blocked "Tell me the story behind the revenue drop" | Low | The rule no longer matches "the story"; "tell me a story" is still blocked. Both are test cases in `test_guard.py` | ✅ |
| 16 | `/trace` names a model step after the first configured model, not the one that answered | Low | First explained in the documents. Fixed in phase 18: a model step is named after the model that answered, and `unanswered` if none did. Test: `test_a_model_step_in_the_trace_is_named_after_the_model_that_answered` | ✅ |
| 17 | Small mismatches: the suite's duration, the name of the budget step in a trace, the mixed-order note | Low | The decision log says three seconds; a trace names the limit reached (`calls`, `tokens` or `time`), with tests; the mixed-order note is also in DESIGN 3.2 | ✅ |
| 18 | A query requested in the same step as a delete is run but its result is not used | Low | First recorded as a trade-off. Fixed in phase 18: after the user's decision the model answers the rest, and the application's outcome of the delete is shown first, also when the model fails. Five tests in `test_delete_flow.py` | ✅ |
| 19 | The cost cap and the automatic rollback lacked detail | Low | DESIGN 3.5 defines the worst case of a step; DESIGN 3.8 says the rollback waits for a minimum number of answers | ✅ |
| 20 | Found while recording session 3: "Why did our churn rate spike last month?" ran eight queries, reached the work limit and showed nothing | Medium | The model is now told when its last step has come (`LAST_STEP` in `agent/graph.py`), and the trace records it. Test: `test_the_model_is_told_when_its_last_step_has_come_so_the_work_ends_in_an_answer`. Recorded again, the question ends in an answer | ✅ |

Three points were raised as more than a minimum prototype needs, and were left as they are: the plan and tracker themselves (they are the record of how the work was done), the planted patterns in the mock data (the tests for "why" questions depend on them), and the scrubber's patterns for card and social security numbers (a few lines, tested).

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
| 2026-10-04 | Phases 15 and 16: every document revised, diagrams and links checked, example sessions re-recorded, fresh clone verified |
| 2026-10-04 | Phase 17: two review passes over the finished repository; 19 findings recorded above |
| 2026-10-04 | Phase 17: the findings were fixed or recorded. A third session was recorded with the brief's own questions; it showed a question ending at the work limit with nothing to show, which led to the last-step rule (D-21). Full verification repeated |
| 2026-10-04 | Phase 18: the three points left open in phase 17 were fixed (deadline, trace naming, a query asked for with a delete); inline comments added to every module; the tests were checked by breaking forty rules on purpose |
| 2026-10-04 | Phase 18: two independent code reviews; their findings fixed or recorded; tests added for the chat loop and the Gemini adapter; the break check grown to 95 rules and added to the repository |
| 2026-10-04 | Phase 18: recording the sessions again showed a question lost to two queries in one call; fixed, and all three sessions recorded with the final code |
| 2026-10-04 | Phase 18: two more reviews, of the documents against the code and of coverage of the brief; their findings fixed or recorded; the design gained a section on running the system; the sessions recorded once more with a follow-up, a product comparison and an off-topic question; full verification repeated |
| 2026-10-04 | A page was added that goes through the brief item by item, with the status of each and where to see it (`REQUIREMENTS.md`) |
