# Demo script (5–7 minutes)

Historical May–July 2026 sample in `data/`. Communications are simulated.

## 0. Start

```bash
PYTHONPATH=. streamlit run app/streamlit_app.py
```

The sidebar has one menu entry per persona under **Personas**. Each has its own pulse, copilot thread, and default horizon. Advanced → Data Health if asked about grain.

## 1. Transport manager (≈2 min)

1. Sidebar → **Transport manager**. Last 7 days.
2. Pulse KPIs, then the **Vendors** table sorted by OTA.
3. Inspect the worst vendor. Drill shows delay reasons (signals, not root cause), offices/shifts, impacted trip ids.
4. **Propose follow-up**, then **Approve** / **Simulate**. Nothing is emailed.
5. **Ask copilot about this vendor** — the thread is scoped to that vendor.

## 2. Line manager (≈2 min)

1. Sidebar → **Team / line manager**. Office and shift default to the busiest pair.
2. Boarded / no-show / late pickup (pickup delay, not late-to-office). Optional strip of vendors driving late pickups on this shift.
3. Inspect a masked rider (`Rider-A7F2 · trip …`). No `stwid`.
4. Propose rider follow-up. There are no phone numbers in the files.

## 3. Facilities head (≈2 min)

1. Sidebar → **Transport & facilities head**. 30 days.
2. Ranked scorecard: billed spend, cost/km, OTA, SLA gap. Spend is invoice actuals, not budget.
3. Inspect a high cost/km or low OTA vendor, then download the Markdown/HTML brief (browser print if a PDF is wanted).
4. Electric-trip share is a sustainability proxy, not emissions.

## Close

Actions & Audit is the full queue. Threads: `transport_manager-copilot`, `facilities_head-copilot`, `line_manager-copilot`.
