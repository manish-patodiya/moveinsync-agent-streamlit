# Mobility Pulse — Transport Operations Agent

Streamlit prototype for a transport manager. The app loads fixed CSVs from `data/` into an in-memory DuckDB database, then runs a LangGraph workflow:

**Sense** (deterministic SQL) → **Reason** (LLM or templates on aggregated evidence only) → **Act** (programmed, simulated actions) → **Daily Brief**.

Email and alert actions are simulated in this prototype; no real external message is sent.

## Architecture

```mermaid
flowchart TD
    csvs["data/*.csv"] --> ingest[Clean + aggregate]
    ingest --> duckdb["DuckDB :memory:"]
    duckdb --> spine["ride_trips_clean"]
    ingest --> aggs["per-trip aggregates"]
    spine --> m360["mobility_trip_360"]
    aggs --> m360
    m360 --> graph["LangGraph"]
    graph --> sense[Sense]
    sense --> reason[Reason]
    reason --> act[Act]
    act --> brief[Daily Brief]
    graph --> ui[Streamlit dashboard]
```

Sense never calls an LLM. Reason receives only structured issue evidence — never raw CSV rows, employee IDs, or unaggregated records. Act applies a fixed policy; “automatic email” means generate and mark a simulated draft, not SMTP/Gmail/Teams.

## One-row-per-trip grain

`ride_data_trip` is the trip spine (one row per trip). Employee, billing, alert, and feedback files are one-to-many.

Correct join path: clean each source → aggregate children to one row per `trip_id` → left-join those aggregates onto cleaned trips → `mobility_trip_360`.

Invariant (hard fail if broken):

```text
COUNT(*) FROM mobility_trip_360
    == COUNT(DISTINCT trip_id) FROM ride_trips_clean
```

The three monthly ride files can repeat the same `trip_id`. Bootstrap keeps one row per `trip_id` (latest `trip_date`) and records how many extra rows were collapsed.

## Data files

Place CSVs (or symlinks to them) in `data/`:

- `emp_data.csv` (source file may be named `emp_Data.csv`)
- `bill_data.csv`
- `alerts_data.csv`
- `Ride_data _trip-*.csv` (May / June / July discovered by glob, not a single hardcoded month)
- `trip_feedback.csv`

No file-upload UI.

## Setup

```bash
./setup.sh
```

Creates `.venv`, installs dependencies, and copies `.env.example` to `.env` (without overwriting an existing one). Or do it manually:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
streamlit run app/streamlit_app.py
```

Bootstrap verification (no UI required):

```bash
PYTHONPATH=. python -m core.bootstrap.app_context
```

## Environment variables

Copy `.env.example` to `.env`. Reason reads only these, and `core/reason/llm_provider.py` is the single place an LLM is constructed:

| Variable | Purpose |
|---|---|
| `LLM_PROVIDER` | `groq` (default), `ollama`, or `openai` |
| `LLM_MODEL` | e.g. `llama-3.3-70b-versatile` (Groq) or `qwen:14b` (Ollama) |
| `GROQ_API_KEY` | only when `LLM_PROVIDER=groq` |
| `OLLAMA_BASE_URL` | defaults to `http://localhost:11434`; only used when `LLM_PROVIDER=ollama` |
| `OPENAI_API_KEY` | only when `LLM_PROVIDER=openai` |

Default setup (Groq, hosted):

```bash
export GROQ_API_KEY=...
```

To run fully local with Ollama/Qwen instead, set `LLM_PROVIDER=ollama` and `LLM_MODEL=qwen:14b` in `.env`, then:

```bash
ollama pull qwen:14b
ollama serve
```

Structured output is enforced via each provider's JSON-schema constrained decoding, so the model must return a valid `IssueReasoningOutput`. If the provider is unreachable, the model misbehaves, or validation fails, Reason falls back to deterministic templates and the UI labels which path produced each explanation. Raw trip rows and employee identifiers are never sent to the model.

Reasoning latency depends on the selected provider — a local Ollama model runs roughly 10–15 seconds per issue, while Groq's hosted inference is typically faster. `app.max_issues_for_reasoning` in `config/settings.yaml` caps how many issues are explained per run.

## Dashboard

- **Command Centre:** filters, an agent run timeline showing what each node did, KPI cards, an issue-type breakdown, and the issues split into "Agent focus" (reasoned and actioned) versus "All detected".
- **Issue Investigation:** the deterministic evidence behind a finding, SLA/peer/baseline context, allowed action types, and the manager explanation.
- **Actions and Audit:** editable drafts plus the approval, review and simulated-send lifecycle.
- **Data Health:** source coverage, cleaning exceptions, unmatched keys, and grain status.
- **Live Alerts:** a session-only watcher that replays synthetic unacknowledged alerts,
  prioritizes them by severity and event type, and exposes fixed acknowledge/escalate/call
  controls. Contact details are deliberately masked synthetic demo data.
- **Operations Copilot:** a checkpointed chat interface over five curated, parameterized
  read-only tools: trip lookup, trip safety alerts, alerts, OTA, and SLA breaches. Results
  can be downloaded as CSV and action recommendations enter the existing human approval
  lifecycle.

Every run records a trace per node (status, duration, what it did), so the Sense → Reason → Act path is visible in the UI rather than hidden in logs. Each issue card is badged with whether the explanation came from the LLM or the deterministic template.

Sense detects configured punctuality breaches (overall/vendor/office/shift), critical safety events, and billing anomalies. Numeric KPIs and severity rules are always deterministic. Only the top configured high/critical issues are sent to Reason.

### Alert demo behavior

`alerts_data.csv` is historical, so Phase 1 does not claim it is a live event source. The
Live Alerts page replays five synthetic events into Streamlit session state at five-second
intervals. Acknowledgement, escalation and calls are also session-only simulations:

- Acknowledge records a local timestamp.
- Escalate asks the configured LLM for a draft from alert and trip context, then **Approve &
  Simulate Send** records it locally and acknowledges the alert.
- Call Driver / Call Employee displays a masked dummy number and records a simulated call.

No source CSV is modified and no message or call leaves the application.

### Copilot safety model

The model is never a database agent. It cannot generate arbitrary SQL, mutate DuckDB, or
access raw employee-leg records. Each question runs a three-node graph:

1. **Route.** Explicit wording is classified by deterministic rules, so a question naming a
   trip ID and the word "safety" always reads that trip's alerts. The LLM is consulted only
   for questions the rules cannot classify, and an unclassifiable follow-up stays on the
   trip from the previous turn. Explicit periods, trip IDs and filters extracted from the
   text always override whatever the model returned.
2. **Query.** Application code runs fixed parameterized SQL for the chosen `ChatTool` and
   computes the verified summary.
3. **Answer.** The LLM writes the reply from the question plus those verified rows. It is
   told the row ordering, forbidden from computing new figures, and cannot contradict the
   summary. Each reply shows the verified figures alongside it; without a reachable LLM the
   deterministic summary is the answer.

Conversation checkpoints are written to `.mobility_pulse/chat_memory.duckdb` by a DuckDB
implementation of LangGraph's `BaseCheckpointSaver`, so a thread keeps its context across a
restart. The published `langgraph-checkpoint-duckdb` package is not used because it pins an
incompatible `langgraph-checkpoint`. Any recommended action is created as `PROPOSED` and
must be approved or rejected by the manager.

## Automatic action booleans (`config/settings.yaml`)

- `auto_create_manager_alerts`: create an in-app manager alert for high/critical issues.
- `auto_create_email_drafts`: create an email **draft** for eligible vendor/safety issues (not a real send).
- `auto_mark_approved_actions_simulated_sent`: if true, an approved draft can move to `SIMULATED_SENT` automatically; if false, it stays `APPROVED` until the manager presses Simulate Send.

Emails always require human approval before `SIMULATED_SENT`.

## Tests

```bash
PYTHONPATH=. pytest tests/ -q
```

Tests do not require LLM credentials.

## Extensibility

Streamlit can be replaced by FastAPI + React/Next.js without rewriting ingestion, DuckDB analytics, the LangGraph workflow, or the central LLM provider.
