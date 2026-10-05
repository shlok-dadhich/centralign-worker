# CentrAlign Worker

Interview prototype: natural-language task in, headed Chromium against two local mock apps, code-side policy + deterministic verifier.

## Setup / run

Python 3.11+. Ollama running with a tool-calling model.

### Windows one-click launcher (recommended)

From the repository root, double-click **`run.cmd`** (or run it in a terminal). It creates the
virtual environment, installs dependencies and Chromium on first use, warns if Ollama is missing,
and then gives you a menu:

```
1. Launch mock apps + Web UI   (recommended)
2. Run a CLI demo task
3. Run eval scenarios A-G
4. Run unit tests
5. Exit
```

Option 1 starts the mock apps and the web UI in their own windows and opens
http://127.0.0.1:8000. Use option 2 if you prefer the terminal experience.

### Manual setup

```
cd centralign-worker
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m playwright install chromium
copy .env.example .env
```

The application reads configuration from process environment variables; it does not automatically
load `.env`. The `.env.example` file is a reference. Set any overrides in the shell before starting
the app. For example, in PowerShell: `$env:OLLAMA_MODEL = "your-installed-model"`.

At startup, the Ollama client prefers `OLLAMA_MODEL`, then documented defaults, then models reported
by the local Ollama service. The current validation machine resolved this to `gpt-oss:20b-cloud`.
A reachable Ollama service and a model that supports tool calling are required.

P0 (Ollama tool call):

```
python p0_tool_call.py
```

Mock apps (leave running):

```
python -m mockapps.run_apps
```

- Portal: http://127.0.0.1:3001  `ap@demo.test` / `demo123`
- Finance: http://127.0.0.1:3002  `fin@demo.test` / `demo123`
- Admin toggles: http://127.0.0.1:3002/admin

P2 browser smoke:

```
python p2_observe.py
```

Happy-path CLI:

```
python cli.py "Find the latest invoice from Acme Corp and enter it into the finance system."
```

Approvals/questions are stdin. Pay task:

```
python cli.py "Find the latest invoice from Acme Corp, enter it into the finance system, and pay it."
```

Unit tests (no LLM, no browser) — 24 tests:

```
pytest -q
```

Eval (apps + Ollama + headed browser):

```
python eval/scenarios.py
```

UI:

```
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000

## Architecture

```
          +-----------+         +------------------+
task ---> | worker.py | <-----> | ollama (tools)   |
          | loop      |         +------------------+
          +----+------+
               |
     +---------+----------+------------------+
     v                    v                  v
browser.py           policy.py          verifier.py
(Playwright)      (READ/WRITE/$$)    (SQLite, no LLM)
     |                                       |
     v                                       v
portal :3001                          portal.db + finance.db
finance :3002
```

`cli.py` and `server/main.py` call the same `Worker.run()`. State is `runs/<id>/state.json` plus
`events.jsonl`, screenshots, and `trace.zip`. The model only emits one validated tool call per turn;
Python executes it.

For a complete component-by-component explanation, end-to-end sequence diagrams, file map,
approval/retry behavior, and current verification evidence, see [`explain.md`](explain.md).
The application-form response draft is in the workspace root at [`../SUBMIT.txt`](../SUBMIT.txt);
replace its personal-info placeholders and add the repository/video links before submitting.

## Design decisions

- **Custom loop vs framework:** The loop is one file and a real multi-turn tool-calling conversation — the assistant's tool calls and the tool results (page observations) stay in message history, so the model always sees its own actions and their outcomes. LangChain/graph runtimes would hide control flow and violate the interview constraint.
- **No scripted task fallback:** task actions are chosen by the model and executed through the browser; there is no hidden API shortcut. A narrowly scoped automatic retry only repeats a browser submit control after the mock finance app reports its configured transient-save error. When the model answers in prose instead of calling a tool, the text is treated as a `finish()` claim and the deterministic verifier still decides the outcome.
- **Label-based transient-save retry:** after that configured error, the worker re-observes and retries the same control by its observed label (`browser.click_by_label`), so regenerated element IDs do not force a stale-ID selector. Other stale-ID errors are classified for model-guided recovery.
- **External explicit state:** `WorkerState` is a dataclass written to disk every step so a failure is inspectable without replaying the model.
- **Deterministic verifier:** `finish()` is a claim. Ground truth is portal SQLite (latest invoice by date for the resolved vendor) vs finance SQLite. Ambiguous or missing vendor is confirmed by a human before browser actions, and the verifier only accepts a vendor fact found in that recorded answer. No LLM in the check.
- **Policy in code:** Financial clicks are blocked until `request_approval` returns approve. Retry cap is per `tool+args` signature in Python, not a prompt suggestion.
- **Generic tools:** Agent code has no vendor names or invoice CSS selectors. Task knowledge lives in mock apps, seed data, and the verifier lookup.
- **Lower-variance runs:** the model is called with `temperature=0` by default. This reduces sampling
  variability but does not guarantee an identical trajectory across model/server versions.
- **Resilient transport:** transient upstream errors (cloud 5xx, dropped connection, timeout) are retried with backoff in `agent/llm.py`; a permanent failure ends the run in a recorded terminal state instead of leaving it hanging.

See `docs/decisions.md`.

## Models / frameworks used

- Python 3.11+, FastAPI, Jinja2, SQLite
- Playwright sync Chromium (headed)
- `ollama` Python package, native tool calling; planner uses `format="json"`
- No Node, Docker, LangChain, vector DB, RAG, multi-agent

## Assumptions

- Ollama is local and the chosen model actually returns tool calls.
- Mock apps stay on localhost:3001 / :3002.
- One user, one headed browser context per run.
- Amounts are integer rupees; dates are `YYYY-MM-DD`.
- "Latest" means latest `date` column in portal SQLite, not list order.

## Known limitations

- Mock apps only; not a real ERP.
- Small local models occasionally answer in prose instead of emitting a tool call. The loop nudges once, then treats the prose as a `finish()` claim that the deterministic verifier still checks — so a wrong answer is caught, not accepted.
- Single-user, no auth on `/admin` POSTs (intentional for eval).
- Observation is DOM text + labeled controls, not vision. CSS-heavy or canvas UIs would be invisible.
- A financial action the human already decided on cannot be re-litigated: the model may re-ask at most twice, then the loop terminates and reports (`DECISION_STALL_CAP`).
- Headed Chromium must be able to open on this machine.

Formerly listed limitations, now solved with real mechanisms (no scripted shortcuts):

- **Wrong saved record is recoverable.** The finance app has an `/records/{id}/edit` page and the agent is told (RULE 14 / `VERIFY_FEEDBACK`) to fix a wrong record in place instead of creating a duplicate — which the verifier would reject anyway.
- **Transient-save retry is label-collision-safe.** `browser.click_by_label(label, tag, role)` re-finds the submit control by label *and* prefers the same tag/role, so a re-rendered page with two same-labelled controls picks the same kind of control.
- **Approvals can't leak through `ask_user`.** Any `ask_user` whose text classifies as financial/irreversible is blocked by policy; the model is instructed to use `request_approval` instead. After a decision, re-asks are bounded and the loop terminates.

## Next steps

- Durable workflow state (resume after process crash)
- Vision fallback when labeled controls are missing
- Per-company learned workflows
- Multi-tab
- Evals at scale
- Real RBAC approvals

## AI-tool usage

The initial implementation was developed with Cursor (Grok) from `PLAN.md`; subsequent stabilization
and documentation work also used GitHub Copilot CLI. The human author remains responsible for
reviewing and submitting the project. Prompts in `agent/prompts.py` are based on the plan, and agent
code does not hardcode invoice-specific selectors or vendor names.

## Eval table

`python eval/scenarios.py` (apps + Ollama + headed browser). The model runs at temperature 0 by
default (`OLLAMA_TEMPERATURE` to override). Two consecutive full A–G runs passed in the current
environment (14/14 scenario executions); latest run:

| scenario | pass/fail | steps | retries | seconds |
|---|---|---|---|---|
| A (Acme happy) | pass | 22 | 0 | 30.1 |
| B (Globex happy) | pass | 21 | 0 | 28.8 |
| C (fail_first_save, transient 500) | pass | 22 | 1 | 30.1 |
| D (rename_button) | pass | 22 | 0 | 29.8 |
| E (ambiguous → ask_user) | pass | 22 | 0 | 30.2 |
| F (pay → approve) | pass | 27 | 0 | 35.0 |
| G (pay → reject) | pass | 24 | 0 | 35.8 |

Unit tests: 24 passed. Both matrix runs used unique browser profiles. The FastAPI UI root route
returned HTTP 200 in a smoke check. Machine-readable
numbers are in `eval/results.json`; the table is rewritten on every eval run. These checks establish
prototype readiness in the current environment, not production readiness or a fresh-machine install
guarantee.
