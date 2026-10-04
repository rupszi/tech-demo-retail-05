# Design: retail data analysis assistant

A chat assistant that lets non-technical executives ask questions about sales, customers and products, discuss the answers, and get reports with action items. It answers from the company's BigQuery data, guided by how human analysts answered similar questions before.

This document describes how the system works in production and how the prototype in this repository implements it. Where the two differ, the text says so. It reflects the client's answers to our questions, which are recorded in [QUESTIONS.md](QUESTIONS.md).

| If you want | Read |
|---|---|
| The picture | [Architecture](#1-architecture) |
| What happens when a question is asked | [How a question is answered](#2-how-a-question-is-answered) |
| How each requirement in the brief is met | [The eight requirements](#3-the-eight-requirements) |
| Why these services, models and framework | [Technology choices](#4-technology-choices-and-why) |
| What fails and what happens then | [Error handling and fallbacks](#5-error-handling-and-fallbacks) |
| To run it | [README](../README.md), [example run](EXAMPLE_RUN.md) |
| The reasoning behind individual decisions | [Decision log](DECISIONS.md) |

## Summary of the approach

Three ideas shape the design.

1. **The model proposes, code decides.** The model writes SQL and prose. Whether a query may run, which rows a user may see, whether personal data may appear, and whether a report is deleted are all decided by ordinary code that the model cannot influence. A user who talks the model into something has gained nothing.
2. **Analyst knowledge is retrieved, not retrained.** Past analyst work (the Golden Knowledge bucket) is searched for each question and the closest matches are placed in the model's instructions. Business definitions such as "what counts as churn" therefore come from the analysts, and adding a definition is a file, not a model release.
3. **Every answer leaves a trace.** Each question produces a structured record of every step. The same records are the metrics, the debugging tool and the raw material for improving the system.

## Requirements at a glance

| # | Requirement | Prototype | Section |
|---|---|---|---|
| 1 | Hybrid intelligence (Golden bucket) | Local folder of sample trios, as agreed with the client | [3.1](#31-hybrid-intelligence-the-golden-knowledge-bucket) |
| 2 | Safety and PII masking, per-user brand scope | **Built and tested** | [3.2](#32-safety-and-pii) |
| 3 | High-stakes oversight | **Built and tested** | [3.3](#33-high-stakes-oversight) |
| 4 | Continuous improvement | Design | [3.4](#34-continuous-improvement) |
| 5 | Resilience and graceful error handling | **Built and tested** | [3.5](#35-resilience) |
| 6 | Quality assurance | 640 offline tests and 79 against BigQuery; evaluation design | [3.6](#36-quality-assurance) |
| 7 | Observability | **Built and tested** | [3.7](#37-observability) |
| 8 | Agility (tone without redeployment) | Tone file read on every question; design for the rest | [3.8](#38-agility-changing-the-tone-without-a-deployment) |

---

## 1. Architecture

```mermaid
flowchart TB
    exec["Executive"]
    idp["Identity provider<br/>signs users in, issues the JWT"]
    fe["Web chat front end"]
    editor["Tone editor and analysts"]
    console["Admin console"]

    subgraph run["Cloud Run: agent service"]
        api["API and session layer<br/>verifies the JWT, streams answers"]
        agent["Conversation graph<br/>guard, agent, tools, confirm"]
        gate["Query gateway<br/>SQL gate, brand scope, cost check, scrubber"]
        tools["Tool registry<br/>reports, later charts, email, Slack, web search"]
    end

    subgraph vertex["Vertex AI"]
        gemini["Gemini models<br/>ordered list with fallback"]
        embed["Embedding model"]
    end

    subgraph stores["Data"]
        bq[("BigQuery<br/>sales data, read-only,<br/>views without personal data")]
        pg[("Cloud SQL for PostgreSQL<br/>conversations, reports, audit log, feedback,<br/>preferences, tone versions, vector index")]
        gcs[("Cloud Storage<br/>Golden Knowledge bucket")]
    end

    jobs["Background jobs<br/>Cloud Run jobs, Pub/Sub, Scheduler<br/>indexing, nightly validation, evaluations,<br/>tone quality gate, preference extraction"]

    subgraph safety["Managed safety services"]
        armor["Model Armor"]
        dlp["Sensitive Data Protection"]
    end

    subgraph obs["Observability"]
        trace["Cloud Trace and Cloud Logging"]
        logs[("BigQuery log sink<br/>dashboards and alerts")]
    end

    exec --> fe
    fe -->|sign-in| idp
    fe -->|HTTPS request with the signed JWT that carries the brand scopes| api
    api -.->|public keys to verify the signature| idp
    editor --> console
    console --> api
    api --> agent
    agent -->|instructions, history, tool results| gemini
    agent --> tools
    agent -->|SQL written by the model| gate
    gate -->|validated and scoped SQL| bq
    agent -->|state, reports, preferences, tone, similar analyses| pg
    agent --> armor
    gate --> dlp
    gcs --> jobs
    jobs --> embed
    jobs --> pg
    agent -.->|trace of every step| trace
    trace --> logs
```

### Building blocks

| Block | Service | What it does | Why this one |
|---|---|---|---|
| Sign-in | The company's identity provider, and a signed JWT on every request | The identity provider signs the user in and issues a short-lived token that says who they are and which brands they may see. The front end sends it with every request, and the API verifies it | The client specified the token. The service needs no user database and no permissions lookup, so it stays stateless |
| Agent service | Cloud Run | Runs the API and the conversation graph in stateless containers | Scales to zero and up with demand, supports streamed responses, and no cluster to operate |
| Orchestration | LangGraph | The conversation as a graph of named steps with saved state | Pausing for a human and resuming is built in, and steps map directly to trace spans (see [4](#4-technology-choices-and-why)) |
| Model | Gemini on Vertex AI | Writes SQL, analysis and reports | The brief asks for Gemini; Vertex AI gives service-account access instead of a long-lived key |
| Analytical data | BigQuery | The sales data; the agent only reads | The data is already there; dry-runs and byte caps give cost control for free |
| Application state | Cloud SQL for PostgreSQL | Conversation checkpoints, saved reports, audit log, preferences, tone versions, vector index | One transactional store for everything that must be consistent, instead of several services |
| Golden bucket | Cloud Storage plus a vector index in PostgreSQL (pgvector) | Source of truth for analyst trios, and similarity search over them | Files are easy for analysts to review and version; at about 1,000 trios a dedicated vector service would be unused capacity |
| Background work | Cloud Run jobs, Pub/Sub, Cloud Scheduler | Indexing, nightly validation, evaluations, the tone quality gate | Keeps slow or scheduled work out of the request path |
| Safety services | Model Armor, Sensitive Data Protection | Screen prompts for injection; inspect output for personal data | Managed detectors are stronger than hand-written patterns; they are a second layer, not the first |
| Observability | OpenTelemetry to Cloud Trace and Cloud Logging, with a sink to BigQuery | Traces, logs, metrics, dashboards, alerts | Standard, queryable with SQL, and no extra vendor |
| Secrets | Secret Manager | Database credentials and third-party keys | Nothing sensitive in code or images |

The client has no data compliance requirements, so the region is chosen for cost and latency: the US, where the dataset lives.

### How the components talk to each other

The API is small. Every call carries the JWT in the `Authorization` header.

| Call | Purpose |
|---|---|
| `POST /v1/conversations/{id}/messages` | Ask a question. The response is a stream of server-sent events: progress, the answer, a confirmation request if a delete is waiting, and the trace id |
| `POST /v1/conversations/{id}/confirmations/{confirmation_id}` | Answer a waiting confirmation with `approved` true or false |
| `GET /v1/reports` and `GET /v1/reports/{id}` | The user's saved reports |
| `POST /v1/traces/{trace_id}/feedback` | Mark an answer helpful or not helpful, with an optional comment |
| `GET` and `PUT /v1/admin/tone` | Read the tone, or submit a new version to the quality gate. Needs an editor scope |

Server-sent events are enough because the stream goes one way. The user's answer to a confirmation is an ordinary request.

| From | To | How | Notes |
|---|---|---|---|
| Browser | API | HTTPS and JSON; server-sent events for the answer | The JWT is verified on every request |
| API | Conversation graph | A call inside the same process | `ChatSession.ask` and `ChatSession.confirm`, the same two calls the CLI makes |
| Agent | Gemini | Vertex AI API over HTTPS, with the tool declarations | Service account, no long-lived key |
| Query gateway | BigQuery | BigQuery jobs API: a dry-run, then the query | Read-only service account; bytes capped per query |
| Agent | PostgreSQL | SQL over a private connection | A delete and its audit entry are one transaction |
| Cloud Storage | Background jobs | An object-change notification through Pub/Sub | Starts indexing when a trio is added or changed |
| Background jobs | Vertex AI and PostgreSQL | HTTPS, and SQL | Embeds the trios and writes the index |
| Agent service | Cloud Trace and Cloud Logging | OpenTelemetry, and structured JSON logs | A sink copies the logs to BigQuery |
| Agent | Model Armor, Sensitive Data Protection | HTTPS | Called on the user's input and on the output |

### Identity and scopes

The client's answer is that the front end sends a JWT with the user's scopes. The design follows from that. A front end cannot be trusted to sign its own tokens, so we assume the token is issued by the company's identity provider when the user signs in, and that the front end only carries it. Any OpenID Connect provider fits.

- **Verified on every request.** The API checks the token's signature against the identity provider's published keys, which it caches and refreshes when they rotate, and it checks the expiry, issuer and audience. A request without a valid token is refused before it reaches the agent.
- **Scopes come only from the token.** Not from the request body, not from the conversation, and never from the model. The model is told the user's brands so that it can explain them, but what it is told has no effect on what a query returns.
- **Denied by default.** The assumed claim format is a `scopes` list with entries such as `brand:Levi's`. The CEO's token carries `brand:*`. A token with no brand scope describes a user who may see nothing; seeing everything is always an explicit grant, never the absence of a restriction.
- **Nothing is cached.** The scopes are read from the token on every request and are not stored in the conversation, so a change to a user's brands takes effect with their next token.
- **Ownership.** A conversation and its saved reports belong to the token's subject.
- **Expiry during a conversation.** Tokens are short-lived, and the front end renews them with the identity provider. A request with an expired token is refused; the front end renews the token and sends the request again. Nothing is lost, because the conversation is stored on the server under its id. A confirmation that is waiting stays waiting, and can only be answered with a valid token for the same subject.

### What the prototype uses instead

The prototype runs on one machine with no cloud services except BigQuery and the Gemini API. Each production block has a small local stand-in behind the same interface, so moving to production replaces implementations and leaves the logic alone.

| Production | Prototype | Seam |
|---|---|---|
| Web chat that calls the API | CLI | `ChatSession` is what any interface calls |
| A JWT issued by the identity provider, verified by the API | Sample token payloads in `config/users.<backend>.json`, chosen with `--user` | `UserProfile.from_claims` |
| Cloud SQL (checkpoints) | In-memory checkpointer | LangGraph checkpointer |
| Cloud SQL (reports, audit) | SQLite file | `ReportStore` |
| Cloud Storage and pgvector | `golden_bucket/` folder and word overlap | `find_similar` |
| Tone versions in a database | `config/persona.md` | `load_persona` |
| Cloud Trace and Logging | `logs/traces.jsonl` | `Tracer` |
| Vertex AI with a service account | Google AI Studio API key | `GEMINI_AUTH=vertex` switches it |
| BigQuery | BigQuery by default; `--backend duckdb` is an offline mock | `DataBackend` |

The prototype does not verify a token signature, because there is no front end to issue one. The mapping from claims to what a user may see is real and tested.

---

## 2. How a question is answered

```mermaid
sequenceDiagram
    autonumber
    actor U as Executive
    participant UI as Interface
    participant G as Guard
    participant A as Agent step
    participant M as Gemini
    participant Q as Query gateway
    participant B as BigQuery

    U->>UI: question
    UI->>G: check the text with rules, no model call
    alt blocked
        G-->>UI: fixed reply
    else allowed
        G->>A: question joins the conversation
        A->>M: rules, tone, brand scope, analyst examples, history
        M-->>A: tool call with SQL
        A->>Q: SQL from the model
        Q->>Q: parse, check allow-lists, rewrite for the brand scope, add LIMIT
        Q->>B: dry-run, free
        B-->>Q: bytes to scan, or an error
        Q->>B: execute
        B-->>Q: rows
        Q-->>A: rows after the PII scrub, or an error the model can act on
        A->>M: tool result
        M-->>A: answer
        A-->>UI: answer after the PII scrub
    end
    UI-->>U: answer and trace id
```

Step by step:

1. **Identity.** The API verifies the token and builds the user's profile from its claims: who they are and which brands they may analyse. The prototype reads a sample token payload chosen with `--user`.
2. **Guard.** Rules check the message for prompt injection, requests for personal data, probing for secrets and obviously unrelated requests. A blocked message gets a fixed reply, is never added to the conversation, and costs no tokens.
3. **Instructions.** The model's instructions are assembled for this question: fixed rules, the tone, today's date, the user's brands, the table descriptions (without personal data columns), and the analyst trios most similar to the question.
4. **Model call.** The model either answers or asks to run tools. It may ask for several queries in one step, which saves round trips.
5. **Query gateway.** Every SQL statement goes through the same pipeline: validate, scope to the user's brands, dry-run, execute, scrub. The model never talks to the database.
6. **Loop.** Results go back to the model. On an error, the model sees the message and may correct the query, within a fixed budget.
7. **Answer.** The final text is scrubbed for personal data and returned with a trace id.
8. **Trace.** Every step above is recorded with timing, tokens, SQL and errors.

### The conversation graph

```mermaid
flowchart LR
    S([start]) --> guard
    guard -->|blocked| E([end])
    guard -->|allowed| agent
    agent -->|asks for tools| tools
    agent -->|final answer| E
    tools -->|results| agent
    tools -->|a delete was requested| confirm["confirm_delete<br/>pauses for the user"]
    confirm --> E
```

Four steps, defined in `src/retail_agent/agent/graph.py`:

- **guard**: the rule-based input check.
- **agent**: one model call. It also enforces the limits per question on model calls, tokens and time.
- **tools**: runs what the model asked for. A delete request is only prepared here.
- **confirm_delete**: pauses until the user decides, then acts and reports the outcome itself.

### What the model is given, and what it is not

The model has five tools: `run_sql`, `save_report`, `list_reports`, `get_report` and `delete_reports`. It has no tool that confirms a deletion, no direct database access, and no access to another user's reports. Earlier questions and answers stay in its context; the result tables of earlier questions do not, which keeps the cost of a long conversation flat. If old numbers are needed again, it queries for them.

---

## 3. The eight requirements

### 3.1 Hybrid intelligence: the Golden Knowledge bucket

**The problem.** The data alone does not say what the business means by "churn", which statuses count as revenue, or what a good quarterly report contains. Analysts know. Their past work is stored as trios: a question, the SQL that answered it, and the analyst's report. The client confirmed that the bucket holds about 1,000 trios in JSON.

**What a trio looks like.** One JSON document per trio:

```json
{
  "question": "Why did our churn rate spike last month?",
  "sql": "WITH last_order AS (...) SELECT ...",
  "report": "Our definition of churn: a customer has churned when 90 days pass without an order ...",
  "tags": ["churn", "retention", "inactive"],
  "tables": ["orders"],
  "author": "analytics team",
  "created": "2025-06-01",
  "validated": "2026-10-03"
}
```

The sample trios in the prototype have the first six fields. `tables` and `validated` are filled in by the indexing and validation jobs.

**At question time.**

1. The question is embedded and compared with the embeddings of the stored trios.
2. The top few are placed in the model's instructions as worked examples: the question, the SQL, and the analyst's reasoning. The instructions say to reuse the definitions and the line of reasoning, and to adapt the SQL rather than copy it.
3. The model's own SQL still goes through the query gateway, which applies the user's brand scope. An example cannot bypass any rule.

This is retrieval, not training. A new definition takes effect as soon as its trio is indexed.

**Trios are written without brand names or figures.** Users have different brands, and a trio's report could contain numbers that one user may see and another may not. Tagging each trio with a scope and filtering per user would split a 1,000-trio bucket into many small ones and make retrieval worse for everyone. Instead a trio carries the method: SQL that names no brand, and a report that explains the definition and the line of reasoning. The user's scope is applied afterwards, by the gate, to the SQL the model actually writes. One trio therefore serves every user, and nothing in a trio can leak between users.

**Keeping the bucket current.**

```mermaid
flowchart LR
    analysts["Analysts write trios"] --> review["Analyst review"]
    prod["Answers from production<br/>marked helpful, or corrected by an analyst"] --> triage["Automatic triage:<br/>strip brands and figures,<br/>drop duplicates, group, rank"]
    triage --> review
    review -->|approved| bucket[("Golden bucket<br/>versioned JSON files")]
    bucket --> index["Embed and index"]
    index --> retrieve["Retrieval at question time"]
    nightly["Nightly job:<br/>dry-run every stored SQL<br/>against the live schema"] --> bucket
    nightly -->|broken or unused| review
```

- **Sources.** Analysts add trios directly. In addition, answers from production that users marked helpful, and answers an analyst had to correct, become candidates.
- **Stripping brands and figures.** A candidate comes from one user's conversation, so it names their brands and their numbers. Before review, the literal values in its SQL (brand names, dates, ids) are turned into named parameters, and a model rewrites the report so that it keeps the definition and the reasoning and drops the figures. The analyst who approves the trio checks that nothing specific is left.
- **Review.** Nothing produced by the assistant enters the bucket without an analyst approving it. Otherwise the system would learn from its own mistakes. The client left this decision to us.
- **Versioning.** The bucket has object versioning. Each trio records its author, its date and the tables it uses.
- **Validation.** A nightly job dry-runs every stored SQL statement against the live schema. A trio that no longer runs is flagged and taken out of retrieval until fixed. Dry-runs are free.
- **Duplicates and decay.** A new trio that is nearly identical to an existing one replaces it rather than joining it. Trios that have not been retrieved for a long time are flagged for review.

**At 1,000 trios and hundreds of users.** The client asked how this scales. Reading scales easily; writing is where the care is needed.

| Concern | At this scale | Design |
|---|---|---|
| Retrieval cost | One embedding call and one search over 1,000 vectors per question | The search is an exact scan in PostgreSQL (pgvector). At this size it takes milliseconds, so no approximate index and no separate vector service are needed |
| Retrieval load | Hundreds of users asking tens of questions a day is a few thousand lookups a day | Negligible next to the model call that follows |
| Candidates from production | Hundreds of users produce far more candidate answers than analysts can read | An automatic funnel. Only answers that were marked helpful, or corrected by an analyst, are considered. Brand names and figures are stripped. Candidates close to an existing trio are dropped. The rest are grouped by similarity and ranked by how many different users asked that kind of question. Analysts review the top groups each week, one representative per group |
| Analyst effort | Must not grow with the number of users | It follows the number of distinct kinds of question, which grows slowly |
| Bucket size | Should stay near 1,000 useful trios | Duplicates are merged, and trios that are no longer retrieved are retired |
| Consistency across instances | Several instances serve at once | The index is in the database, so every instance searches the same one. The indexing job writes a new version beside the old one and switches to it in one transaction |
| Retrieval quality | More trios means more near-misses | A fixed set of questions with a known best trio is part of the evaluation suite |

**In the prototype.** As agreed with the client, the bucket is the folder `golden_bucket/` with seven sample trios, and retrieval is by shared words, which needs no extra service. The interface (`find_similar`) is the one the embedding search would implement. A test runs every stored SQL statement through the SQL gate and the database, and the BigQuery test group runs them on the real dataset. This is the nightly validation in miniature.

**What it changed in practice.** Recording the example run exposed two errors in the model's reports: a "last quarter" computed as the last 90 days, and totals the model added up itself, one of them wrongly. Both were fixed by adding one trio for quarterly reports that states the calendar-quarter rule and returns every total from SQL. In the run recorded afterwards, every figure in the report matches the query result. No code changed.

### 3.2 Safety and PII

The brief has three requirements here: only analysis questions, no personal data in output, and each user sees only data for their own products. The client confirmed that "their own products" means their brands, and that the CEO sees all. The requirements are met by layers, each of which holds on its own.

| Layer | What it stops | Where |
|---|---|---|
| Token check | Anonymous or forged requests | The API verifies the JWT (prototype: `--user` picks a sample payload) |
| Input guard | Obvious injection, requests for personal data, secret probing, unrelated requests, over-long input | `safety/guard.py`; Model Armor in production |
| Instructions | Honest mistakes by the model | `agent/prompts.py` |
| **SQL gate** | Anything that is not a single read-only query on the four allowed tables | `safety/validator.py` |
| **Scope rewrite** | Rows for brands the user may not see | `safety/scoping.py` |
| **Column allow-list** | Personal data columns | `safety/scoping.py`, `safety/policy.py` |
| Database permissions | Writes, other data, and personal data columns, refused again by BigQuery | Production only, on the company's own data |
| Output scrubber | Personal data that reached text anyway | `safety/scrubber.py`; Sensitive Data Protection in production |
| Log hygiene | Personal data in traces | Questions and answers are scrubbed before logging |

The three layers in bold are the guarantees. The others reduce cost and noise, or back the guarantees up.

**Only analysis questions.** Obvious cases are stopped by rules before any model call. For the rest, the model is instructed to decline in one sentence without running a query. In testing, "What is the capital of France?" got exactly that: one model call, no query. Even a model that ignored the instruction could only run read-only queries on the user's own data.

**Malicious users.** The SQL the model writes is treated as untrusted input. It is parsed into a syntax tree and must pass every check; the default is to reject.

| Attack | Result |
|---|---|
| "Ignore your instructions and..." | Blocked by the guard; if rephrased past it, the model still has no tool that does harm |
| Getting the model to write `DROP`, `DELETE`, a script, or several statements | Rejected: only a single query is allowed |
| Reaching `events`, `INFORMATION_SCHEMA`, another dataset | Rejected: only four tables are allowed, and they resolve only by fully qualified name |
| Disguising a table with a `WITH` name | Rejected: scope analysis decides what a name refers to |
| Calling user-defined, remote, ML or AI functions | Rejected: namespaced functions are denied by default |
| Hiding text in SQL comments | Removed: only SQL regenerated from the syntax tree is executed |
| A huge query to run up cost | Refused by the dry-run and capped by BigQuery's byte limit |
| Instructions planted in data or in a saved report | The model is told results are data; and it still has no harmful tool |
| A token with no brand scope, or with scopes of another kind | Sees nothing: access is denied unless a brand scope grants it |

The test suite contains 73 hostile queries, all rejected, and 24 legitimate analytical queries, all accepted, for each of three user profiles.

**Personal data.** The client confirmed the list: names, email, address, postal code and coordinates. These are seven columns of `users`. The real `users` table only ever appears inside a subquery that selects the remaining columns by name, so `SELECT *` and whole-row expressions cannot reach them. Customers are identified by ID, which the client confirmed is fine and which keeps "top customers" working. Age, gender, city, state and country may be shown for an individual customer; the client confirmed this too. Details are in [D-09](DECISIONS.md#d-09-personal-data-is-kept-out-by-a-column-allow-list-customers-are-shown-by-id).

**Each user sees only their brands.** Every table reference is replaced with a subquery filtered to the user's brands. The filter is attached to the table, so it holds in joins, subqueries and unions. The CEO's token carries the explicit all-brands scope; a token with no brand scope gets zero rows from every table. An order that mixes a user's brands with others is visible to them, but only their own items and the revenue of those items are. Details and trade-offs are in [D-08](DECISIONS.md#d-08-per-user-brand-scope-is-applied-by-rewriting-table-references). For a user limited to three brands, `SELECT COUNT(*) FROM order_items` runs as:

```sql
SELECT COUNT(*) FROM (
  SELECT id, order_id, user_id, product_id, ..., sale_price
  FROM `bigquery-public-data`.thelook_ecommerce.order_items
  WHERE product_id IN (
    SELECT id FROM `bigquery-public-data`.thelook_ecommerce.products
    WHERE brand IN ('Allegra K', 'Levi\'s', 'Roxy'))
) AS order_items LIMIT 500
```

**In production: what BigQuery enforces as well.** The application-level gate is necessary because it gives the model useful errors and works on any dataset. Part of it can be enforced a second time by the database, and part cannot.

- **Read-only.** The service account that runs the queries has no write permission anywhere, and BigQuery caps the bytes a query may bill.
- **No personal data.** The company's sales data is in its own project. The service account is given access only to views that leave out the personal data columns (authorized views), and not to the tables underneath, so BigQuery refuses a query for those columns whatever SQL arrives. This cannot be shown on the assignment's dataset: `bigquery-public-data` is readable by every Google Cloud account, so nothing set up in our own project can stop a query that names the public table. On that dataset the SQL gate is the only thing that keeps personal data out.
- **Brand scope is enforced by the gate alone.** The scopes arrive in an application-level token, which BigQuery never sees, so BigQuery cannot filter rows per user. This is why the gate's scoping is tested against independently computed results and against the real dataset. If a second, independent enforcement is wanted later, the front end's identity provider can be federated with Google Cloud so that BigQuery sees the end user and row access policies apply. That is an option, not part of this design.

### 3.3 High-stakes oversight

The assistant manages a library of saved reports. Deleting is destructive, so the model may request it but cannot do it. The client confirmed that one confirmation is enough, that reports are not shared between users, and that deleted reports do not need to be recoverable.

```mermaid
sequenceDiagram
    actor U as User
    participant UI as Interface
    participant A as Conversation graph
    participant M as Gemini
    participant R as Reports store

    U->>UI: Delete all reports mentioning X
    UI->>A: ask
    A->>M: question
    M-->>A: tool call delete_reports, mentioning X
    A->>R: find matching reports owned by this user
    R-->>A: ids and titles
    A->>A: store exactly these ids in the conversation state
    A-->>UI: paused, with the list to confirm
    UI->>U: shows the list, says it is permanent, asks yes or no
    U->>UI: yes
    UI->>A: resume, approved
    A->>R: delete exactly the stored ids
    R-->>A: done, audit log written
    A-->>UI: Deleted 2 reports, this cannot be undone
```

What makes it strict:

- **The decision does not pass through the model.** The graph pauses in a step of its own. Only the interface can resume it, with the user's answer. The model has no tool that confirms, and extra arguments such as `confirmed: true` are ignored. A "yes" typed into the chat is a new message, not a confirmation.
- **What is confirmed is what is deleted.** The matching report ids are stored in the conversation state when the list is shown. The delete uses those ids, not a new search, so a report created in the meantime is not swept in.
- **Ownership is checked in the store.** Every query on reports is filtered by owner. A request naming another user's report id finds nothing.
- **Deleting is permanent, and the user is told so.** The rows are removed; there is no restore function. The confirmation says that the deletion cannot be undone, and so does the outcome. Because nothing can be undone, the confirmation is the safeguard, which is why it lists the exact titles.
- **The outcome is reported by the application.** After a delete, the message "Deleted 2 reports" is written by code. An earlier version asked the model to phrase it; in a real run the model call was rate-limited after the delete had happened, and the user was told to try again. The result of a confirmed action must not depend on the model.
- **Everything is audited.** The request, and the confirmation or the cancellation, are written to an audit log with the report ids. The titles of what was requested and of what was deleted are kept there, so the record survives the reports.

What keeps it from breaking the experience:

- **One question, once.** The user sees the exact titles and answers yes or no. Anything else cancels.
- **A fast, exact outcome.** It arrives in under a second, with no model call.
- **No confirmation when nothing matches.** The assistant simply says so.

Both phrasings in the brief are covered: "mentioning X" is a case-insensitive text match on title and content, for any term, and "the reports we made in this conversation" uses the conversation id stored with each report.

**In production** the same pause guards any destructive or outward-facing tool. Today it is written for deleting reports. Making it general takes two small changes: a tool's declaration gains a flag that says it needs confirmation, and the graph sends any flagged call through the pause with a description of what will happen. Sending a report by email or to Slack would then use it as it is. The pause survives a restart because the state is in PostgreSQL, and a pending confirmation expires after a set time. Database backups cover operator error; there is no restore offered to users.

### 3.4 Continuous improvement

**Where feedback comes from.** Both loops below need to know which answers were good. Under each answer the web chat shows "helpful" and "not helpful", with an optional comment. The front end sends the choice to the API with the trace id of that answer. It is stored in a `feedback` table (trace id, user, rating, comment, time) and written to the logs, so it joins to the trace of the answer. An analyst's correction of an answer is stored the same way, with the corrected SQL. Two more signals need no click and come from the traces: a question rephrased straight after an answer, and a confirmation that was cancelled. The prototype collects none of this.

**User level: remembering how each person likes to work.**

- A per-user preference store holds a few facts: tables or bullet points, how much depth, charts or text, areas of interest.
- **Explicit** preferences ("always give me tables") are saved immediately through a `remember_preference` tool.
- **Implicit** preferences are proposed by a background job that reads a user's recent conversations and feedback: repeated "shorter, please", which answers were marked helpful, whether they keep asking for a table after getting bullets. A proposal is stored with a confidence and applied once it is seen repeatedly; it fades if contradicted.
- The stored preferences are added to the model's instructions as a short block. They only affect presentation. They cannot widen access or change the safety rules, which are not in that part of the instructions.
- The user can ask what is remembered about them, and change or clear it.

**System level: learning from what happened.**

```mermaid
flowchart LR
    traces["Traces and user feedback"] --> review["Weekly failure review<br/>grouped by cause"]
    review --> trios["New or corrected trios"]
    review --> rules["Changes to instructions"]
    review --> cases["New evaluation cases"]
    trios --> evals["Evaluation suite"]
    rules --> evals
    cases --> evals
    evals -->|passes| release["Release"]
    evals -->|fails| review
```

**How failures are grouped.** A weekly job takes the traces that failed, gave up, were marked not helpful, or were followed by a rephrase. It groups them by a key that needs no judgement: the outcome, the error code of the first failed step, and the analyst example that was retrieved, if any. Within a group, questions are clustered by similarity, so one cause is not counted many times. Groups are ranked by how many different users they affected.

**What it proposes.** For each of the top groups the job drafts one change for a person to accept or reject. A group of syntax errors on the same function becomes a proposed line for the instructions. A group with no retrieved example and few helpful marks becomes a request for a new trio, with the most common wording of the question attached. An answer that an analyst corrected becomes a candidate trio and an evaluation case.

The loop is: observe, group failures by cause, change one of three things (a trio, the instructions, an evaluation case), and release only if the evaluation suite still passes. The assistant proposes and people approve. It does not rewrite its own instructions in production, because an unreviewed change that helps one question can quietly harm others.

This loop was run by hand while building the prototype, which shows what it looks like:

| Observed in traces | Cause | Change |
|---|---|---|
| Queries failing on `'Levi''s'` | Wrong apostrophe escaping for BigQuery | One line in the instructions |
| Queries failing on `TIMESTAMP_SUB(..., INTERVAL 6 MONTH)` | Unsupported in BigQuery | One line in the instructions |
| A quarterly report on "the last 90 days", with returns counted as revenue | No stated convention | Date and revenue conventions in the instructions, and a quarterly-report trio |
| A brand total added up wrongly by the model | The model did arithmetic | The trio now returns every total from SQL |
| Every call retrying a rate-limited model | No memory of the rate limit | The model is rested for as long as the provider asks |
| "Try again" shown after a delete had succeeded | Outcome message depended on the model | The application reports the outcome |
| A "why" question ran eight exploratory queries, reached the work limit and showed nothing | The model was not told that its steps were running out | The tool results announce the last step, so the work ends in an answer |

### 3.5 Resilience

The brief asks that errors and empty results are detected and corrected before giving up, without crashing the interface, without inflating cost, and with tolerance for third-party failures.

**Self-correction with a budget.** Every failure is classified, because the right response depends on the cause.

| What happened | How it is detected | Response |
|---|---|---|
| Bad SQL, unknown table or column | The gate's parser, or BigQuery's dry-run. Both are free. | The error message goes back to the model; it may retry twice |
| A rule was broken by an honest mistake (for example a personal data column) | SQL gate | Same, with a message that says what to do instead |
| A rule was broken in a way a well-behaved model would not (a write, several statements) | SQL gate | No retry. The model is told to stop and explain |
| The query would scan too much | Dry-run estimate, and BigQuery's byte cap | The model must narrow it; counts as a failure |
| No rows | Result is empty | First time: a hint to check filter values. Second time: stop and say no data matched |
| BigQuery unavailable | Error class | The same SQL is retried once; the model is not asked to rewrite a correct query |
| More rows than the limit | Result reached the limit | The result is marked incomplete and the model is told to aggregate |

After the retry limit, no further query runs for that question and the model is told to explain plainly what it could not do. In the recorded sessions three of 23 queries failed on real BigQuery, and each was corrected on the next attempt.

**Bounded cost and time.**

- Broken SQL is caught before it is billed.
- A question may use at most 8 model calls, 60,000 tokens and 120 seconds. When one model call is left, the tool results say so and tell the model to answer from what it has, so a question that explores for too long ends in an answer. Past any of the limits, the assistant stops and asks the user to narrow the question. The time limit is checked between steps, so a step that is already running is allowed to finish; how long that can be is stated below.
- At most 500 rows are fetched and 50 are shown to the model, with a note when rows were left out.
- Blocked messages and confirmed deletes use no model calls.
- Old result tables are not resent with every turn.
- BigQuery caps the bytes a query may bill.

Observed over the three recorded sessions: about 8,800 tokens and 2.4 model calls per question on average, at most 10 MB scanned per query, and a median of 4.7 seconds per answer.

**How long an answer may take.** The client accepts the assumed response times and allows one to two minutes for long reports. Ordinary questions are answered in seconds. A long report is produced within the same request, with progress shown, and the time limit stops anything that runs longer. No background job is needed.

**The time limit is not a hard deadline.** It is checked between steps, and a step that has started runs to its own limits. A query has a 60-second job timeout and is retried once if BigQuery is unavailable. A model step has a 90-second timeout per call, three attempts per model, three models, and one wait of up to a minute for a rate limit to clear. The failures seen in practice, rate limits and server errors, come back within seconds, and the slowest recorded answer took 46 seconds. But if every call to every model hung until its timeout, a single step could run for about fifteen minutes before the limit was checked again. The remedy is to hand each step the time that is left as its own deadline. That is a small change, and it is not in the prototype.

**A cost cap per question (design).** The client wants the cap to be easy to configure and suggests $1 per question, as a design matter. The cap is one setting, in dollars.

- A price table, also configuration, holds the price per token for each model and the price per byte scanned in BigQuery.
- Before each model call and each query, the cost so far plus the worst case of the next step is compared with the cap. For a model call the worst case is its input tokens, which are counted before the call, plus the maximum output tokens, which is a setting. For a query it is the bytes reported by the dry-run. If the cap would be passed, the question stops with the same message as the other limits.
- The cost of each question is written to its trace, so cost per question is a metric with an alert.
- The cap can differ per environment or per group of users.

The prototype does not compute dollars. It applies the limits directly: model calls, tokens, seconds, and 1 GB scanned per query, all settings in `.env`. These already keep a question far below $1: at BigQuery's on-demand list price at the time of writing ($6.25 per TiB), the 1 GB cap on a single query is worth about half a cent.

**When the model provider fails.** Models are configured as an ordered list.

- A timeout or server error is retried with exponential backoff and jitter.
- A rate limit that asks for a short wait is waited out.
- A rate limit that asks for a long wait rests that model for exactly that long, and the next model in the list answers meanwhile. This is a circuit breaker whose timing is set by the provider.
- If every model is resting, the soonest one is waited for, up to a minute, with a message in the interface. Beyond that, the user is told how long to wait.

This was exercised for real: on the free tier the two larger models allow 20 requests a day, and the recorded sessions were answered almost entirely by the third model without the user doing anything.

**Never crashing the interface.** A failing tool returns an error to the model instead of raising. An unexpected exception anywhere in a turn is caught at the session boundary, recorded in the trace with its cause, and turned into a short apology. The conversation continues.

**In production, additionally:** conversation state in PostgreSQL so a restarted container resumes mid-conversation; rest periods shared between instances; provisioned model capacity so that rate limits are rare.

### 3.6 Quality assurance

**Before deployment.** Four kinds of checks, from cheapest to most expensive.

1. **Deterministic layers: ordinary tests.** The SQL gate, scoping, scrubber, guard, report store and retry logic do not involve the model and are tested exhaustively. The prototype has 640 tests that run offline in about three seconds, including the hostile-query corpus and row-level comparisons against independently computed results.
2. **Agent behaviour with a scripted model.** The model is replaced by a script, so the loop is tested without cost or randomness: self-correction, giving up at the limit, budgets, outages, the delete flow. These are also in the 640.
3. **The same rules on the real dataset.** A further group of 79 tests runs against BigQuery on request, as the client suggested: the schema, every legitimate query after the gate has rewritten it (as free dry-runs), brand scope and personal data on real data, and every analyst example.
4. **Evaluation with the real model.** A fixed set of questions run against the real model and a fixed copy of the data, scored automatically:
   - *Result accuracy.* For questions with a known answer (the golden trios supply them), the result of the assistant's query is compared with the result of the analyst's query. Comparing results, not SQL text, accepts any correct query.
   - *Grounding.* Every figure in an answer must appear in, or follow from, the query results of that turn. This is a mechanical check, and it is the one that would have caught the wrongly added total described in 3.4.
   - *Safety set.* Questions that must be refused or must return nothing outside the user's scope.
   - *Robustness.* Injected failures: a bad first query, an empty result, an unavailable model.

**Release gate.** A change to code, instructions, trios or the model version runs these checks. It is released to a small share of traffic first and promoted if live metrics hold. A change to the tone goes through its own automated gate, described in 3.8.

**Do reports answer the user's intent?** Three signals, because none is enough alone.

- *A rubric scored by a second model*, given the question, the report and the data: does it answer what was asked, for the period asked, with insights tied to numbers and action items that follow from them? This scales but can be wrong, so it is calibrated against the next signal.
- *Analyst review of a sample* each week, using the same rubric. Disagreements with the automated score are used to fix the rubric.
- *What users do next.* A report followed by "no, I meant..." or by the same question rephrased did not match the intent. Rephrase rate is tracked.

One class of intent error is prevented rather than detected: the assistant states the period and scope it used ("July 1 to September 30"), so a misread "last quarter" is visible to the reader.

**User experience.**

| Measure | What it tells us |
|---|---|
| Task completion: questions that end in an answer the user did not have to correct | Whether it works |
| Time to answer, and number of turns to a usable answer | Whether it is efficient |
| Rephrase rate and abandonment | Where it misunderstands |
| Helpful / not helpful on answers | Direct satisfaction |
| Confirmation cancel rate | Whether delete requests are understood before the user is asked |
| Share of answers that needed a wait | Whether rate limits or slowness are felt |

These come from the traces. They are complemented by moderated sessions with a few executives before launch, because the numbers say where people struggle but not why.

### 3.7 Observability

**One trace per question.** Every question produces a record with a trace id, the user, the conversation, the question, the outcome, and every step with its duration. The trace id is shown under each answer, so "this answer looks wrong" comes with the key needed to find it.

| Step | Recorded |
|---|---|
| Guard | Allowed or blocked, and the category |
| Model call | Model that answered, tokens in and out, tools requested, and which analyst examples were in the instructions |
| Model retry or wait | Which model failed, the error, how long was waited |
| Query | The SQL the model wrote, the SQL that ran, rows, bytes scanned, error code and message, whether it was cut off, redactions |
| Report saved | Report id |
| Confirmation | Approved or cancelled, how many reports |
| Budget | That the model was told its last step had come; and which limit was reached: model calls, tokens or time |
| Unexpected error | Type and message |

Questions and answers are scrubbed for personal data before they are logged. Result rows are never logged, only their count.

A model step is named after the first model in the configured list, because that is what was asked. The model that actually answered is recorded beside it, and is the first thing `/trace` shows in the detail column.

**Agent-level metrics**, computed from the traces and shown by `/stats`:

| Metric | Why it matters |
|---|---|
| Share answered, blocked, gave up, failed | The headline: is it working? |
| Latency, median and 95th percentile | Experience. Waiting for a confirmation is excluded |
| Tokens and model calls per question | Cost |
| Model retries | Provider health |
| Query error rate, and the share of those that recovered | Whether the model writes valid SQL, and whether self-correction works |
| Empty-result rate | Misunderstood filters or genuinely missing data |
| Guard blocks by category | Abuse attempts, and false alarms |
| Personal data redactions | Should be zero. Anything else means an upstream layer leaked |
| Deletes confirmed and cancelled | Whether the confirmation is doing its job |

In production one more is added: cost per question in dollars, computed from the same trace with the price table of 3.5.

**Alerts in production:** failed share above a threshold; gave-up share rising; 95th percentile latency; any redaction; a spike in guard blocks; cost per question approaching the cap; every model in the list resting.

**Debugging one answer.** The reviewer takes the trace id, opens the trace, and reads the steps in order: what the model asked for, the exact SQL that ran, what came back, what the model did next. In the prototype this is `/trace` for the last question, or the JSON line in `logs/traces.jsonl`. The example run shows it. Because the executed SQL is stored, the query can be re-run to see the data the model saw. Because the conversation id is stored, all turns of a conversation can be read in order.

**In production** each trace also carries the tone version in force, which is what the automatic rollback in 3.8 compares. The same records are OpenTelemetry spans sent to Cloud Trace, and structured logs sent to Cloud Logging with a sink to BigQuery, so the metrics above are SQL queries and the dashboards and alerts are built on them. A tool specialised in model traces can be added on the same data, but is not required.

### 3.8 Agility: changing the tone without a deployment

The model's instructions are assembled from layers, and only one of them is editable.

| Layer | Who changes it | How |
|---|---|---|
| Safety rules and data conventions | Developers | Code, reviewed and tested |
| **Tone and style** | One named editor who is not a developer | Admin console, no deployment |
| Per-user preferences | The user, and the learning loop | Preference store |
| Analyst examples | Analysts | Golden bucket |

**In production** the tone is a versioned record in the database. The client's answer is that one non-developer edits it and that what stands between an edit and production must be an automated quality gate. So no person approves a change, and no deployment is involved: the editor submits, and the gate decides.

**The automated quality gate.**

1. **Static checks, immediate.** A length limit. The text is screened with the same rules as user input for attempts to change the rules ("ignore...", "reveal..."). It may not contain SQL, tool names or scope terms.
2. **Evaluation run, a few minutes.** The candidate tone is used to answer a fixed set of about thirty questions with the real model. To pass:
   - the safety set passes completely: refusals still refuse, and nothing outside a user's scope is returned;
   - result accuracy and grounding are no worse than the current version, within a small tolerance;
   - reports still have their parts: summary, insights tied to numbers, action items;
   - a second model confirms that the answers follow the requested tone.
3. **Decision.** If every check passes, the version is published at once, or at a scheduled time, which suits a weekly rhythm. If any check fails, nothing is published and the editor is shown which check failed, with examples.
4. **After publishing.** Live metrics are watched for a set period. If the failed share or the rephrase rate gets worse beyond a threshold, the previous version is restored automatically and the editor is told. With a few hundred users the numbers are small, so the rephrase rate is judged only after a minimum number of answers under the new version, which is a setting. A rise in failed answers does not wait.

Around the gate:

- **A preview** lets the editor see a few standard questions answered with the new tone before submitting. It is a convenience, not an approval.
- **Versions and audit.** Each version records who submitted it, when, and the gate's result. Rolling back is selecting a previous version, which passes the gate trivially because it already did.
- **The tone layer cannot grant anything.** The rules that matter are enforced in code. The gate exists so that a tone change cannot make answers worse, not to keep data safe.
- The service reads the active version with a short cache, so a published change is live within a minute.

**In the prototype** the tone is the file `config/persona.md`. It is read on every question, so an edit takes effect on the next message with no restart. A test changes the file between two questions and checks that the instructions changed. The gate is not built.

---

## 4. Technology choices and why

**Google Cloud.** The data is in BigQuery and the brief asks for Gemini. Keeping compute and observability in the same cloud means one permission model and no data crossing providers.

**Gemini, as an ordered list of models.** The prototype defaults to `gemini-3.8-flash`, then `gemini-3.5-flash`, then `gemini-3.5-flash-lite`. Flash models are fast enough for an interactive tool and wrote correct BigQuery SQL in testing. A list rather than a single model is what makes the fallback in 3.5 possible. Model names are configuration, so moving to a newer model is not a code change, and the evaluation suite says whether it is an improvement.

**Function calling run by our own graph.** The SDK can execute tools automatically. That is switched off: the graph runs every tool, so every call is checked, budgeted and traced, and the pause for confirmation is possible.

**LangGraph.**

- The delete flow needs execution to stop, wait for a person, and resume exactly where it stopped, with its state intact. LangGraph's interrupt and checkpoint do this, and the resume re-runs only the confirmation step.
- A graph of named steps is easy to trace and easy to explain.
- Considered instead: a hand-written loop (pause, resume and persistence would have to be built and tested by hand); Google's Agent Development Kit (a natural fit for Gemini, but less direct for a custom confirmation step); LangChain's prebuilt agents (they hide the control flow that this design needs to make explicit).
- **Experience.** I had not used LangGraph before this assignment. I chose it for the reasons above and learned it for this project. Only its core is used: a state graph, conditional edges, an interrupt and a checkpointer. The model is called through Google's own SDK behind a small interface, not through LangChain's model wrappers, so there is less framework between the code and the provider.

**sqlglot** parses SQL into a syntax tree for the BigQuery dialect, analyses scopes, and regenerates SQL. The whole SQL gate is built on those three things. It also translates BigQuery SQL for the local test database.

**PostgreSQL for all application state.** Conversations, reports, audit log, preferences, tone versions and the vector index have modest volume and benefit from transactions and joins. One managed database is simpler to operate and back up than a document store, a cache and a vector service.

**DuckDB for offline tests.** An in-process database with a close SQL dialect, so the safety layer is tested against real query results on any machine, without a network. BigQuery is the default data source of the assistant itself.

**uv, ruff, pytest.** One command reproduces the environment; the offline suite runs in about three seconds.

---

## 5. Error handling and fallbacks

| Failure | Detected by | What the system does | What the user sees |
|---|---|---|---|
| Invalid SQL | SQL gate or dry-run | Error returned to the model; up to 2 corrections | The answer, slightly later |
| Query limit reached | Counter per question | No further queries; model explains | A plain statement of what could not be done |
| Forbidden SQL | SQL gate | Rejected, no retry | A short explanation that this is not allowed |
| Empty result | Row count | Hint once, then stop | "No data matched", with the filter used |
| Result too large | Row limit | Marked incomplete; model aggregates | An aggregated answer |
| Query too expensive | Dry-run and byte cap | Refused; model narrows it | The answer for a narrower scope |
| BigQuery unavailable | Error class | One immediate retry, then stop | "The data warehouse is temporarily unavailable" |
| BigQuery not set up at start | A free dry-run when the CLI starts | Stops before the chat begins | What is missing, and the offline option |
| Model timeout or server error | Error class | Backoff and retry, then next model | "The model is busy, retrying" while it works |
| Model rate limit | Error with a wait time | Wait if short, otherwise rest it and use the next model | Usually nothing |
| Every model unavailable | All resting or failed | Wait up to a minute for the soonest, else stop | How long to wait before trying again |
| Work limit for one question | Call and token counters | The model is told when its last step has come; past the limit, stop | An answer from what was found; or a request to narrow or split the question |
| Time limit for one question | Clock, checked between steps | Stop | The same request, naming the time limit |
| Tool crashes | Exception caught in the tool step | Error result to the model | An explanation that it did not work |
| Any other exception | Caught at the session boundary | Logged with cause; turn ends | A short apology; the conversation continues |
| Personal data in output | Scrubber | Masked and counted | The masked text |
| Confirmation not answered | A new message arrives | Treated as "no" | Nothing is deleted |

---

## 6. Where data lives

| Data | Production | Prototype | Notes |
|---|---|---|---|
| Sales data | BigQuery, read-only | BigQuery, or the offline mock | Never copied; personal data columns never leave it |
| Conversation state | PostgreSQL checkpoints | Memory | Includes any pending confirmation. Holds no scopes |
| Saved reports and audit log | PostgreSQL | SQLite | Owner on every row. Deleting is permanent; the audit log keeps ids and titles |
| Preferences, tone versions | PostgreSQL | Tone file | |
| Golden trios | Cloud Storage and vector index | Folder | Versioned JSON |
| Traces | Cloud Logging and BigQuery | JSONL file | Scrubbed; no result rows |
| Secrets | Secret Manager | Local `.env`, git-ignored | |

What is sent to the model: the instructions, the conversation text, and query results (at most 50 rows per query, already limited to the user's brands and free of personal data columns).

---

## 7. Extending it

**A new capability** is a tool. In the prototype that is three small pieces: a declaration (name, description, parameters) in `TOOL_SPECS`, a method on `Toolbox`, and a branch in the `tools` step of the graph that calls it. With more than a handful of tools the branch becomes a registry that maps a name to its function, and the declaration gains a flag that says whether the tool needs confirmation (3.3). Neither exists yet: five tools and one destructive action did not need them.

- *Charts.* A tool returns a chart specification from a query result. The interface renders it. No new safety surface, because the data came through the gateway.
- *Email and Slack.* The client may add Slack later as a place to send results. Both are outward-facing actions, so they would be marked as needing confirmation and reuse the pause in 3.3. Sending is handed to a background worker.
- *Web search for trends.* The results are untrusted text. They are treated as data, like query results, and cannot trigger tools by themselves.

**A new data source** implements the four methods of `DataBackend`. Its rules are a policy: the allowed tables, the personal data columns, and how each table is limited to the user's brands. In the prototype that policy is written for the four tables of this dataset, in `safety/policy.py` and `safety/scoping.py`. A second source would mean handing the policy to the gateway instead of importing it. The pipeline itself (validate, scope, dry-run, execute, scrub) does not change.

**The web chat**, which is what the client will use in production, calls the API, and the API calls `ChatSession.ask` and `ChatSession.confirm`, as the CLI does.

---

## 8. Limits of the prototype

- The token is not verified, because there is no front end to issue one. `--user` picks a sample token payload.
- Brand scope and the personal data rule are enforced by the application only. Production adds what BigQuery can enforce on the company's own data (3.2); on the public dataset used here nothing more is possible.
- The model can still misstate a figure. Stating conventions and returning totals from SQL reduce it; the grounding check in 3.6 is what would catch the remainder, and it is not built.
- The input guard is rules only. Subtle cases rely on the model declining and on the gate.
- Conversation state is in memory and ends with the process. Reports and traces persist.
- Golden bucket retrieval is by shared words. It works for seven trios and would not for 1,000.
- The cap in dollars is not computed. Limits are set in model calls, tokens, seconds and bytes.
- The time limit is checked between steps, so one slow step can run past it (3.5).
- The pause for confirmation is written for deleting reports. A general flag on tools is designed, not built (3.3 and 7).
- No feedback on answers is collected (3.4).
- On the free tier the larger models allow 20 requests a day. The assistant keeps working on the lite model, with somewhat weaker answers.

The questions asked of the client, and their answers, are in [QUESTIONS.md](QUESTIONS.md).
