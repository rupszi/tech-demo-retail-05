"""Checks the tests by breaking the code on purpose.

A large test suite can still prove little: a test may assert something that stays true when the
feature is broken. This script copies the repository to a temporary directory, breaks one rule in
the copy, runs the offline tests, and reports whether a test failed. It does so for every entry
in BREAKS, one at a time. A break that no test notices is a gap in the tests.

    uv run python tests/mutation_check.py

It takes about five minutes and changes nothing in the repository. Each entry is tied to a line
of source text: when that line changes, the script says the break could not be applied and the
entry has to be updated. See docs/DECISIONS.md, D-30.
"""

# ruff: noqa: E501 - the entries quote lines of source, which are clearer unwrapped

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# (what is broken, file, the source text to replace, what to put in its place)
BREAKS = [
    (
        "personal data columns are not rejected up front",
        "src/retail_agent/safety/validator.py",
        "    _reject_pii_columns(tree)\n",
        "    pass\n",
    ),
    (
        "users subquery exposes every column, personal data included",
        "src/retail_agent/safety/scoping.py",
        '    if table == "users":\n        return list(SAFE_USER_COLUMNS)\n',
        "    if False:\n        return list(SAFE_USER_COLUMNS)\n",
    ),
    (
        "no brand filter at all",
        "src/retail_agent/safety/scoping.py",
        "    if profile.all_brands:\n        return None\n",
        "    if True:\n        return None\n",
    ),
    (
        "a user with no brand scope sees everything",
        "src/retail_agent/safety/scoping.py",
        "        return exp.false()  # no brand scope means no access",
        "        return exp.true()",
    ),
    (
        "a token with no brand scope is treated as all brands",
        "src/retail_agent/safety/profiles.py",
        "            all_brands=BRAND_SCOPE + ALL_BRANDS in scopes,",
        "            all_brands=BRAND_SCOPE + ALL_BRANDS in scopes or not granted,",
    ),
    (
        "write statements nested in a query are not detected",
        "src/retail_agent/safety/validator.py",
        "        if isinstance(node, _WRITE_NODES):",
        "        if False:",
    ),
    (
        "tables outside the allow-list are accepted",
        "src/retail_agent/safety/validator.py",
        "        if name not in ALLOWED_TABLES or not qualifier_ok:",
        "        if False:",
    ),
    (
        "a WITH name is trusted anywhere in the query (the original bypass)",
        "src/retail_agent/safety/validator.py",
        "        visible = {name.lower() for name in scope.cte_sources}\n",
        "        visible = {cte.alias.lower() for cte in tree.find_all(exp.CTE)}\n",
    ),
    (
        "a WITH clause may reuse a table name",
        "src/retail_agent/safety/validator.py",
        "    if shadowed:\n",
        "    if False:\n",
    ),
    (
        "namespaced functions are allowed",
        "src/retail_agent/safety/validator.py",
        "            if namespace not in ALLOWED_FUNCTION_NAMESPACES:",
        "            if False:",
    ),
    (
        "table functions in FROM are allowed",
        "src/retail_agent/safety/validator.py",
        "        if isinstance(node, exp.From | exp.Join) and not isinstance(node.this, _FROM_SOURCES):",
        "        if False:",
    ),
    (
        "no row limit is forced",
        "src/retail_agent/safety/validator.py",
        "    return tree.limit(max_rows), max_rows\n",
        "    return tree, max_rows\n",
    ),
    (
        "several statements are accepted",
        "src/retail_agent/safety/validator.py",
        "    if len(statements) > 1:",
        "    if False:",
    ),
    (
        "SQL comments survive into what is executed",
        "src/retail_agent/safety/validator.py",
        'tree.sql(dialect="bigquery", comments=False)',
        'tree.sql(dialect="bigquery", comments=True)',
    ),
    (
        "the input guard lets everything through",
        "src/retail_agent/safety/guard.py",
        "        if pattern.search(normalised):",
        "        if False:",
    ),
    (
        "the scrubber masks nothing",
        "src/retail_agent/safety/scrubber.py",
        "        text, count = pattern.subn(f\"[{kind.replace('_', ' ')} removed]\", text)",
        "        count = 0",
    ),
    (
        "a delete is carried out whatever the user answers",
        "src/retail_agent/agent/graph.py",
        '        approved = isinstance(decision, dict) and decision.get("approved") is True',
        "        approved = True",
    ),
    (
        "a delete uses a fresh search instead of the confirmed ids",
        "src/retail_agent/agent/graph.py",
        '            deleted = deps.reports.delete(owner, pending["ids"])',
        "            deleted = deps.reports.delete(owner, [r.id for r in deps.reports.list(owner)])",
    ),
    (
        "reports are not filtered by owner",
        "src/retail_agent/reports/store.py",
        '        where, params = ["owner = ?"], [owner]',
        '        where, params = ["owner <> ?"], ["nobody"]',
    ),
    (
        "deleting leaves the rows in place",
        "src/retail_agent/reports/store.py",
        "            f\"DELETE FROM reports WHERE owner = ? AND id IN ({', '.join('?' * len(ids))})\",",
        "            f\"SELECT 1 FROM reports WHERE owner = ? AND id IN ({', '.join('?' * len(ids))})\",",
    ),
    (
        "self-correction has no limit",
        "src/retail_agent/agent/tools.py",
        '        if failures > limit:\n            refused = {"error": "The query limit for this question was reached."}',
        '        if False:\n            refused = {"error": "The query limit for this question was reached."}',
    ),
    (
        "forbidden SQL is retried like an honest mistake",
        "src/retail_agent/agent/tools.py",
        "                failures = failures + 1 if e.retryable else limit + 1",
        "                failures = failures + 1",
    ),
    (
        "no limit on model calls or tokens",
        "src/retail_agent/agent/graph.py",
        '        if out_of_calls or state["tokens"] >= settings.turn_token_budget:',
        "        if False:",
    ),
    (
        "no time limit",
        "src/retail_agent/agent/graph.py",
        "        used = time_used(state)\n        if used >= settings.turn_time_budget_s:\n            return out_of_time(used)\n\n",
        "        used = time_used(state)\n\n",
    ),
    (
        "the model call is not given a deadline",
        "src/retail_agent/agent/graph.py",
        "                    time_left=settings.turn_time_budget_s - used,",
        "                    time_left=None,",
    ),
    (
        "retries ignore the deadline",
        "src/retail_agent/llm/resilient.py",
        "            if left <= 0:\n                return None  # out of time; the caller reports it\n",
        "",
    ),
    (
        "no fallback to the next model",
        "src/retail_agent/llm/resilient.py",
        "        for model in self._models:\n            if self._rest_left(model) > 0:",
        "        for model in self._models[:1]:\n            if self._rest_left(model) > 0:",
    ),
    (
        "a rate-limited model is not rested",
        "src/retail_agent/llm/resilient.py",
        "                    self._resting_until[id(model)] = self._clock() + e.retry_after\n",
        "",
    ),
    (
        "the model is not told when its last step has come",
        "src/retail_agent/agent/graph.py",
        '                message["result"] = {**message["result"], "instruction": LAST_STEP}',
        "                pass",
    ),
    (
        "the outcome of a delete is not put in front of the answer",
        "src/retail_agent/agent/graph.py",
        '            if state.get("delete_outcome"):\n                text = f"{state[\'delete_outcome\']}\\n\\n{text}"',
        "            if False:\n                text = f\"{state['delete_outcome']}\\n\\n{text}\"",
    ),
    (
        "a second delete in the same question reaches the user",
        "src/retail_agent/agent/graph.py",
        '                    if pending is not None or state.get("delete_outcome"):',
        "                    if pending is not None:",
    ),
    (
        "blocked messages still reach the model",
        "src/retail_agent/agent/graph.py",
        '        "guard", lambda s: END if s.get("outcome") == "blocked" else "agent", ["agent", END]',
        '        "guard", lambda s: "agent", ["agent", END]',
    ),
    (
        "answers are not scrubbed",
        "src/retail_agent/agent/graph.py",
        "        text, redactions = scrub_text(response.text)  # last check before anything is shown",
        "        text, redactions = response.text, {}",
    ),
    (
        "earlier result tables are resent to the model",
        "src/retail_agent/agent/graph.py",
        '        if m["role"] == "user" or (m["role"] == "assistant" and not m.get("tool_calls"))',
        "        if True",
    ),
    (
        "an exception reaches the interface",
        "src/retail_agent/agent/session.py",
        "        except Exception as e:  # noqa: BLE001 - nothing may crash the interface\n",
        "        except ZeroDivisionError as e:\n",
    ),
    (
        "the wait for a confirmation counts as latency",
        "src/retail_agent/observability/tracing.py",
        "            self._started += time.perf_counter() - self._paused_at\n",
        "",
    ),
    (
        "bare table names resolve on BigQuery (a default dataset is set)",
        "src/retail_agent/data/bigquery_backend.py",
        "            maximum_bytes_billed=self.max_bytes_billed,\n            dry_run=dry_run,",
        "            maximum_bytes_billed=self.max_bytes_billed,\n            default_dataset=self.dataset,\n            dry_run=dry_run,",
    ),
    (
        "no byte cap on BigQuery jobs",
        "src/retail_agent/data/bigquery_backend.py",
        "            maximum_bytes_billed=self.max_bytes_billed,\n            dry_run=dry_run,",
        "            dry_run=dry_run,",
    ),
    (
        "an oversized query passes the dry-run",
        "src/retail_agent/data/bigquery_backend.py",
        "            and job.total_bytes_processed > self.max_bytes_billed\n",
        "            and False\n",
    ),
    # ---- the interface and the Gemini adapter
    (
        "the interface passes the opposite of the user's answer",
        "src/retail_agent/cli/app.py",
        "                result = run(lambda decision=approved: session.confirm(decision))",
        "                result = run(lambda decision=approved: session.confirm(not decision))",
    ),
    (
        "pressing Enter at the confirmation deletes",
        "src/retail_agent/cli/app.py",
        "            default=False,",
        "            default=True,",
    ),
    (
        "a closed input or Ctrl-C at the confirmation deletes",
        "src/retail_agent/cli/app.py",
        "    except (EOFError, KeyboardInterrupt):\n        return False\n",
        "    except (EOFError, KeyboardInterrupt):\n        return True\n",
    ),
    (
        "commands are sent to the model",
        "src/retail_agent/cli/app.py",
        '        if text.startswith("/"):  # commands are handled here and never reach the model',
        "        if False:",
    ),
    (
        "a report can be opened by anyone",
        "src/retail_agent/reports/store.py",
        '        found = self._reports("owner = ? AND id = ?", [owner, report_id])',
        '        found = self._reports("owner <> ? AND id = ?", ["nobody", report_id])',
    ),
    (
        "Ctrl-C during a question ends the chat",
        "src/retail_agent/cli/app.py",
        "        except KeyboardInterrupt:\n            # Ctrl-C while a question is being worked on drops that question, not the chat.",
        "        except ZeroDivisionError:\n            # Ctrl-C while a question is being worked on drops that question, not the chat.",
    ),
    (
        "report titles are read as formatting in the confirmation",
        "src/retail_agent/cli/app.py",
        '        table.add_row(str(report["id"]), escape(report["title"]))',
        '        table.add_row(str(report["id"]), report["title"])',
    ),
    (
        "traces are not tied to the conversation",
        "src/retail_agent/cli/app.py",
        "            tracer=Tracer(settings.trace_dir, conversation_id, profile.user_id, show_progress),",
        '            tracer=Tracer(settings.trace_dir, "cli", profile.user_id, show_progress),',
    ),
    (
        "the model's thinking is shown as part of the answer",
        "src/retail_agent/llm/gemini.py",
        '        text = "".join(p.text for p in parts if p.text and not p.thought)',
        '        text = "".join(p.text for p in parts if p.text)',
    ),
    (
        "tool calls are not sent back as Gemini wrote them",
        "src/retail_agent/llm/gemini.py",
        "            raw=candidate.content.model_dump_json(exclude_none=True),",
        "            raw=None,",
    ),
    (
        "thinking tokens are not counted",
        "src/retail_agent/llm/gemini.py",
        "                (usage.candidates_token_count or 0) + (usage.thoughts_token_count or 0)",
        "                (usage.candidates_token_count or 0)",
    ),
    (
        "the Gemini request has no timeout from the deadline",
        "src/retail_agent/llm/gemini.py",
        "            http_options=types.HttpOptions(timeout=_timeout_ms(time_left)),",
        "            http_options=None,",
    ),
    (
        "the instructions are not sent to Gemini",
        "src/retail_agent/llm/gemini.py",
        "            system_instruction=system,",
        "            system_instruction=None,",
    ),
    (
        "the tools are not declared to Gemini",
        "src/retail_agent/llm/gemini.py",
        "            tools=[types.Tool(function_declarations=declarations)] if declarations else None,",
        "            tools=None,",
    ),
    (
        "network errors are treated as permanent",
        "src/retail_agent/llm/gemini.py",
        '            raise LLMError(f"{self.name}: network error: {e}", transient=True) from e',
        '            raise LLMError(f"{self.name}: network error: {e}", transient=False) from e',
    ),
    (
        "every API error is retried",
        "src/retail_agent/llm/gemini.py",
        "                transient=e.code in _TRANSIENT_CODES,",
        "                transient=True,",
    ),
    (
        "the wait a rate limit asks for is dropped",
        "src/retail_agent/llm/gemini.py",
        "                retry_after=_retry_after(e),",
        "                retry_after=None,",
    ),
    (
        "an empty response is not retried",
        "src/retail_agent/llm/gemini.py",
        '            raise LLMError(f"{self.name}: empty response ({reason})", transient=not refused)',
        '            raise LLMError(f"{self.name}: empty response ({reason})", transient=False)',
    ),
    (
        "an unexpected SDK error skips the fallback",
        "src/retail_agent/llm/gemini.py",
        "        except Exception as e:  # noqa: BLE001 - one model's failure must not end the turn",
        "        except ZeroDivisionError as e:",
    ),
    (
        "tool results go back in the order they finished",
        "src/retail_agent/llm/gemini.py",
        "                last.parts.sort(key=lambda p: order.get(p.function_response.id, len(order)))",
        "                pass",
    ),
    # ---- per-question state
    (
        "the outcome of a delete carries over to the next question",
        "src/retail_agent/agent/graph.py",
        '            "delete_outcome": "",\n',
        "",
    ),
    (
        "tokens carry over to the next question",
        "src/retail_agent/agent/graph.py",
        '            "tokens": 0,\n',
        '            "tokens": state.get("tokens", 0),\n',
    ),
    (
        "empty results carry over to the next question",
        "src/retail_agent/agent/graph.py",
        '            "empty_results": 0,\n',
        '            "empty_results": state.get("empty_results", 0),\n',
    ),
    (
        "the clock of a question starts with the first question",
        "src/retail_agent/agent/graph.py",
        '            "turn_started_at": time.time(),\n',
        '            "turn_started_at": state.get("turn_started_at", time.time()),\n',
    ),
    (
        "tokens written by the model are not counted",
        "src/retail_agent/agent/graph.py",
        '            "tokens": state["tokens"] + response.input_tokens + response.output_tokens,',
        '            "tokens": state["tokens"] + response.input_tokens,',
    ),
    (
        "the last step is announced on the first result only",
        "src/retail_agent/agent/graph.py",
        '            for message in results:\n                message["result"] = {**message["result"], "instruction": LAST_STEP}',
        '            for message in results[:1]:\n                message["result"] = {**message["result"], "instruction": LAST_STEP}',
    ),
    (
        "an unanswered question stays in the history",
        "src/retail_agent/agent/graph.py",
        '        if m["role"] == "assistant" or (i + 1 < len(earlier) and earlier[i + 1]["role"] != "user")',
        "        if True",
    ),
    (
        "the whole history is sent to the model",
        "src/retail_agent/agent/graph.py",
        "    ][-HISTORY_MESSAGES:]",
        "    ]",
    ),
    (
        "a history may start with an answer",
        "src/retail_agent/agent/graph.py",
        '    while answered and answered[0]["role"] != "user":\n        answered.pop(0)\n',
        "",
    ),
    (
        "a new message lets the old turn run on",
        "src/retail_agent/agent/graph.py",
        '        if len(request["tool_calls"]) > 1 and not abandoned:',
        '        if len(request["tool_calls"]) > 1:',
    ),
    (
        "the graph's step ceiling is fixed at 60",
        "src/retail_agent/agent/session.py",
        '            "recursion_limit": 2 * settings.max_llm_calls + 10,',
        '            "recursion_limit": 60,',
    ),
    (
        "an interrupted question leaves no trace",
        "src/retail_agent/agent/session.py",
        '            self.tracer.end_turn("failed", "")\n            raise',
        "            raise",
    ),
    # ---- tools and store
    (
        "a top-N the model asked for is reported as cut off",
        "src/retail_agent/safety/gateway.py",
        "            truncated=len(frame) >= self.max_rows,",
        "            truncated=len(frame) >= query.limit,",
    ),
    (
        "delete arguments are coerced instead of checked",
        "src/retail_agent/agent/tools.py",
        "            isinstance(report_ids, list) and all(_is_id(i) for i in report_ids)",
        "            True",
    ),
    (
        "truthy values count as a yes for all_reports",
        "src/retail_agent/agent/tools.py",
        'args.get("all_reports") is True',
        'args.get("all_reports")',
    ),
    (
        "a report title is not scrubbed",
        "src/retail_agent/agent/tools.py",
        '        title, _ = scrub_text(" ".join(title.split()) or "Untitled report")',
        '        title = " ".join(title.split()) or "Untitled report"',
    ),
    (
        "the delete is not committed with its audit entry",
        "src/retail_agent/reports/store.py",
        '        self.log(owner, "delete", ids, json.dumps([r.title for r in doomed]))\n        return doomed',
        '        self._db.rollback()\n        self.log(owner, "delete", ids, json.dumps([r.title for r in doomed]))\n        return doomed',
    ),
    (
        "the audit log keeps the model's words unscrubbed",
        "src/retail_agent/agent/graph.py",
        "                            detail = json.dumps(scrub_value(requested))",
        "                            detail = json.dumps(requested)",
    ),
    (
        "only the question and the answer of a trace are scrubbed",
        "src/retail_agent/observability/tracing.py",
        "        turn = scrub_value(turn)\n",
        "",
    ),
    (
        "a damaged line makes the whole log unreadable",
        "src/retail_agent/observability/tracing.py",
        "        except json.JSONDecodeError:\n            continue  # a damaged line costs one trace, not the whole log",
        "        except ZeroDivisionError:\n            continue",
    ),
    (
        "an answer after a failed query always counts as a recovery",
        "src/retail_agent/observability/tracing.py",
        "            round(sum(1 for t in had_sql_error if _recovered(t)) / len(had_sql_error), 3)",
        '            round(sum(1 for t in had_sql_error if t["outcome"] == "answered") / len(had_sql_error), 3)',
    ),
    (
        "the median is the minimum",
        "src/retail_agent/observability/tracing.py",
        "    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))] if ordered else 0.0",
        "    return ordered[0] if ordered else 0.0",
    ),
    (
        "tokens per question is not divided by the number of questions",
        "src/retail_agent/observability/tracing.py",
        '        "tokens_per_question": round(sum(t["tokens_in"] + t["tokens_out"] for t in traces) / n),',
        '        "tokens_per_question": round(sum(t["tokens_in"] + t["tokens_out"] for t in traces)),',
    ),
    (
        "the wait for a confirmation is not paused in a real turn",
        "src/retail_agent/agent/session.py",
        "            self.tracer.pause()\n",
        "",
    ),
    # ---- gate, profiles, model layer
    (
        "system variables and parameters pass the gate",
        "src/retail_agent/safety/validator.py",
        "        if isinstance(node, _PARAMETERS):",
        "        if False:",
    ),
    (
        "personal data named outside a column reference passes the first check",
        "src/retail_agent/safety/validator.py",
        "    used = sorted({i.name.lower() for i in tree.find_all(exp.Identifier)} & PII_COLUMNS)",
        "    used = sorted({c.name.lower() for c in tree.find_all(exp.Column)} & PII_COLUMNS)",
    ),
    (
        "a user listed twice silently takes the last entry",
        "src/retail_agent/safety/profiles.py",
        "        if profile.user_id in profiles:  # a second entry would silently replace the first",
        "        if False:",
    ),
    (
        "something close to brand:* grants every brand",
        "src/retail_agent/safety/profiles.py",
        "            all_brands=BRAND_SCOPE + ALL_BRANDS in scopes,",
        "            all_brands=ALL_BRANDS in granted,",
    ),
    (
        "a model that only failed hides one that is due back",
        "src/retail_agent/llm/resilient.py",
        "            soonest = min(rested, key=self._rest_left)",
        "            soonest = min(self._models, key=self._rest_left)",
    ),
    (
        "a wait may end after the deadline",
        "src/retail_agent/llm/resilient.py",
        "            if wait == 0 or (wait <= self._max_delay and wait + 0.5 < self._time_left()):",
        "            if wait == 0 or (wait <= self._max_delay and wait < self._time_left()):",
    ),
    (
        "a model whose rest is already over gets no second chance",
        "src/retail_agent/llm/resilient.py",
        "            if wait == 0 or (wait <= self._max_delay and wait + 0.5 < self._time_left()):",
        "            if 0 < wait <= self._max_delay and wait + 0.5 < self._time_left():",
    ),
    (
        "a query that timed out is treated as an outage and re-run",
        "src/retail_agent/data/bigquery_backend.py",
        '            raise DataError("too_expensive", message) from e',
        '            raise DataError("unavailable", message) from e',
    ),
    (
        "a timed-out job is left running",
        "src/retail_agent/data/bigquery_backend.py",
        "                    job.cancel()\n",
        "                    pass\n",
    ),
    (
        "two queries in one call end the attempts for the question",
        "src/retail_agent/safety/validator.py",
        "            only_queries,\n",
        "            False,\n",
    ),
    (
        "a write hidden among several statements may be retried",
        "src/retail_agent/safety/validator.py",
        "            only_queries,\n",
        "            True,\n",
    ),
    (
        "a wrong setting is thrown as a stack trace",
        "src/retail_agent/cli/app.py",
        "    except ValueError as e:  # for example MAX_ROWS=five",
        "    except ZeroDivisionError as e:",
    ),
    # ---- lookups, metrics and the extension point
    (
        "another user's trace can be opened by its id",
        "src/retail_agent/cli/app.py",
        '        if found and found[-1].get("user") == owner:',
        "        if found:",
    ),
    (
        "the line under an answer does not name the model",
        "src/retail_agent/cli/app.py",
        '    models = f" ({escape(\', \'.join(answered))})" if answered else ""',
        '    models = ""',
    ),
    (
        "a tool in the table of handlers is not called",
        "src/retail_agent/agent/graph.py",
        "                elif name in toolbox.handlers:  # a tool that needs only its arguments",
        "                elif False:",
    ),
    (
        "the trace does not say which tone was in force",
        "src/retail_agent/agent/graph.py",
        '            "tone": hashlib.sha1(persona.encode()).hexdigest()[:8],',
        '            "tone": "same",',
    ),
    (
        "query errors are not broken down by kind",
        "src/retail_agent/observability/tracing.py",
        '        "sql_errors_by_code": _count(s["error"] for s in steps("sql") if s.get("error")),',
        '        "sql_errors_by_code": {},',
    ),
    (
        "expired credentials are treated as the model's mistake",
        "src/retail_agent/data/bigquery_backend.py",
        "    auth_exc.GoogleAuthError,  # expired or missing credentials: rewriting the SQL cannot help\n",
        "",
    ),
]


def copy_repository(work: Path) -> None:
    for name in ("src", "tests", "config", "golden_bucket"):
        shutil.copytree(REPO / name, work / name, ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO / "pyproject.toml", work / "pyproject.toml")


def tests_pass(work: Path) -> tuple[bool, str]:
    """Run the offline suite in the copy. Returns whether it passed, and the line that says why."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"],
        cwd=work,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(work / "src")},  # the copy, not the installed code
    )
    lines = result.stdout.strip().splitlines()
    failed = next((line for line in lines if line.startswith("FAILED")), "")
    return result.returncode == 0, (failed or (lines[-1] if lines else ""))[:150]


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        work = Path(directory)
        copy_repository(work)
        passed, summary = tests_pass(work)
        if not passed:
            print(f"The tests fail before anything is broken: {summary}")
            return 1
        unnoticed = []
        for number, (what, file, old, new) in enumerate(BREAKS, 1):
            path = work / file
            original = path.read_text(encoding="utf-8")
            if original.count(old) != 1:
                print(f"{number:3}. could not be applied (the source changed): {what}")
                unnoticed.append(what)
                continue
            path.write_text(original.replace(old, new), encoding="utf-8")
            passed, summary = tests_pass(work)
            path.write_text(original, encoding="utf-8")  # undo the break before the next one
            print(f"{number:3}. {'NOT NOTICED' if passed else 'caught'}: {what}")
            if passed:
                unnoticed.append(what)
            else:
                print(f"       {summary}")
    print(
        f"\n{len(BREAKS) - len(unnoticed)} of {len(BREAKS)} deliberate breaks were caught by the tests."
    )
    for what in unnoticed:
        print(f"  not caught: {what}")
    return 1 if unnoticed else 0


if __name__ == "__main__":
    sys.exit(main())
