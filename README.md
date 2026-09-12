     

# Project Specification: Autonomous ATC Routing Agent (Santa Barbara Municipal — KSBA)

## 1. Project Objective & Core Concept

**The Concept:** An autonomous multi-agent system that monitors live flight telemetry around Santa Barbara Municipal Airport (KSBA) and simulates the real ATC role chain — Clearance Delivery, Ground, Tower, and Approach — handing an aircraft off between roles the way real controllers do, grounded in live geospatial data and the actual FAA rulebook rather than hardcoded logic.

**The Problem:** Human controllers face extreme cognitive load during airspace disruptions and routine handoffs alike. This project explores whether an LLM-based agent, given real tool access (radar state, regulatory text) and deterministic safety guardrails where math is involved, can reason correctly across a full multi-role ATC workflow — not just format phraseology.

**Defense & Aerospace Alignment:** This architecture mirrors autonomous systems operating in physical space (e.g., routing drone swarms around radar threats): distributed agent orchestration, deterministic safety guardrails layered under LLM judgment, and real-time geospatial processing. It's a portfolio cornerstone for ML/Data Science roles in defense and aerospace.

**Why KSBA, not LAX:** The project originally targeted LAX with a simpler 2-agent (North/South Tower) design coordinating over a geographic split. It was rescoped to Santa Barbara Municipal — a single airport with one real physical runway intersection (7/25 crossed by 15L/33R and 15R/33L) — because that's a cleaner, more precisely-modelable case for the 4-role sequential handoff design this project now implements, and because KSBA's real-world traffic (regional jets + heavy GA) is a better fit for a role-chain than LAX's dual-complex operation.

## 2. Current Status

| Piece                                                                                                       | Status                                                                                                                                                  |
| ----------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| PostGIS geospatial pipeline (schema, OpenSky poller, proximity-conflict trigger, Airflow housekeeping DAGs) | Built, live-verified against real OpenSky data                                                                                                          |
| FAA rulebook RAG + MCP server (`query_radar`, `query_faa_rules`)                                        | Built, live-verified — FAISS index over JO 7110.65 (full) and JO 7360.1 (Ch. 1–2)                                                                     |
| 4-role LangGraph orchestration (Clearance → Ground → Tower → Approach)                                   | Built, live-verified end-to-end against real PostGIS + MCP + Claude Sonnet 5                                                                            |
| Tower's runway-intersection safety check (deterministic PostGIS distance query)                             | Built, live-verified in both directions (must-hold and should-clear)                                                                                    |
| Live dashboard (airport diagram + scenario playback)                                                        | Built, live-verified                                                                                                                                    |
| Recorded-day replay (label a window of real traffic, loop it, agents fire on real role transitions)         | Built, live-verified — see `agent_orchestration/replay.py`                                                                                             |
| Offline reward scoring (Phase 5)                                                                            | Built for the old 2-agent design; currently broken for the 4-role model — `compute_coordination_reward` queries the retired `runway_complex` table, so `score_episode.py` throws on any invocation. See`evaluation/reward.py`'s documented known gap |
| Fine-tuned phraseology model                                                                                | Deferred — see §4                                                                                                                                     |
| Live auto-triggering for Ground/Tower/Approach off real state transitions                                   | Deferred (roadmap) — currently reached via a manual`/trigger/{role}/{icao24}` endpoint                                                               |

`ksba_prototype/` is a standalone sandbox (plain Anthropic SDK calls, no MCP/PostGIS) that validated Claude Sonnet 5's judgment quality on these 4 roles — including the intersection must-hold/should-clear contrast — before the real integrated system was built. It's kept as-is for reference; the production system's prompts were adapted from it.

## 3. The Tech Stack

* **Agentic Framework:** LangGraph (`agent_orchestration/`) — 4 role-specific reasoning nodes, tool-bound to real MCP tools plus deterministic local tools (`finalize_instruction`, `advance_to_next_role`, Tower-only `check_runway_conflict`).
* **LLM:** Claude Sonnet 5, via `langchain-anthropic`. Zero-shot judgment quality (validated in `ksba_prototype/`) has been strong enough that fine-tuning hasn't been necessary so far — see §4.
* **Interoperability:** Model Context Protocol (MCP) Python SDK — `mcp_server/` runs as a long-lived SSE service, not a subprocess.
* **Data & Geospatial:** PostgreSQL + PostGIS (`data_pipeline/`), FAISS (file-based vector index, no separate vector-DB service needed at this corpus size).
* **Infrastructure:** Apache Airflow (minute-scale housekeeping only — not the hot-path poller, see §6's gotchas), Docker & Docker Compose.
* **Dashboard:** Plain HTML/CSS/JS, no build step, served as static files directly from `agent_orchestration`'s FastAPI app (same-origin, no CORS needed).
* **Deferred:** PyTorch/QLoRA fine-tuning — scaffolding (`model_finetuning/`) exists but is unused; see §4.

## 4. On Fine-Tuning

The original plan called for QLoRA fine-tuning Llama 3 on LiveATC transcripts to force strict FAA phraseology. In practice, `ksba_prototype/`'s validation (9 isolated scenarios plus full departure/arrival handoff chains) showed Claude Sonnet 5 already produces correct decisions and standards-compliant phraseology zero-shot, given real tool grounding and good system prompts — including the hardest test, the Tower intersection must-hold/should-clear contrast, which requires genuine spatial threshold reasoning rather than blanket caution.

Fine-tuning would still earn its keep for: lower latency/cost via a smaller model, authentic-sounding audio/voice output, or offline operation without an API dependency. None of those are blocking for what this project demonstrates, so it's parked as optional future work rather than a requirement. `model_finetuning/` stays scaffolded (and a local CUDA GPU is available) if that changes.

## 5. Architecture

### The 4-role chain

* **Clearance Delivery** (132.9) — issues CRAFT-format IFR clearances before pushback. The only role that's structurally unreachable by live radar-based triggering: an aircraft awaiting clearance at the gate typically isn't broadcasting ADS-B yet. Reached only via the manual trigger endpoint.
* **Ground** (121.7) — routes aircraft to their departure runway (commercial jets → 7/25, GA → 15L/15R), enforces hold-short + readback.
* **Tower** (119.7) — sequences takeoffs/landings. Runways 15L/15R/33L/33R physically intersect 7/25 (real seeded geometry in the `runway` table); before clearing 15/33 traffic, Tower calls a deterministic `check_runway_conflict` tool — a real PostGIS distance query against intersecting-runway thresholds, not LLM-estimated distance — consistent with this project's "LLMs cannot do math" guardrail (§6).
* **Approach** (120.55) — sequences inbound IFR traffic and accepts outbound climb-out check-ins, using speed control as the primary separation tool.

Departures flow Clearance → Ground → Tower → Approach; arrivals flow Approach → Tower → Ground. A role hands off via `advance_to_next_role` with an **explicit** target role (not an implicit "next" lookup), so both directions work without special-casing. A handoff records a durable audit row (`coordination_events`) but does not re-invoke the next role's reasoning node in the same run — the next radio exchange happens via a separate trigger, same as real ATC handoffs are separate radio calls.

### Grounding, not hardcoding

Every reasoning node is bound to the same two MCP tools regardless of role: `query_radar` (live PostGIS aircraft state) and `query_faa_rules` (FAISS search over JO 7110.65 / JO 7360.1). Decisions are meant to come from real data and real regulatory text, not prompt-embedded assumptions — the dashboard's transcript panel shows exactly which tools were called for each decision.

### Two trigger paths

1. **Automatic**, via Postgres `LISTEN/NOTIFY` on `anomaly_events` inserts — Tower's runway-intersection rule is itself an anomaly type (`PROXIMITY_CONFLICT`), so this path is live for Tower.
2. **Manual**, via `POST /trigger/{role}/{icao24}` — the only way to reach Clearance, and the mechanism the dashboard uses to drive scenario playback. Live automatic triggering for Ground/Tower/Approach off real phase-of-flight transitions is roadmap, not yet built.

### Recorded-day replay

`data_pipeline/replay/record_session.py` labels a `[start, end)` window of already-recorded `aircraft_state_history` as a `recording_session` — recording itself is just the poller running continuously, so this is a naming step, not a separate capture mode. `agent_orchestration/replay.py`'s `ReplayController` then plays that window back on a clock, re-populating `aircraft_state_current` tick by tick so the *real* pipeline fires against real traffic: the same proximity-conflict trigger, `LISTEN/NOTIFY` path, and phase-of-flight role classifier used for live data, unchanged. Role transitions detected during playback (an arrival crossing from Approach's range into Tower's, etc.) are turned into real agent runs; each decision is cached by a content-derived fingerprint, so the first pass through a window costs one API call per event and every subsequent loop is free. The dashboard's "Recorded Day" panel drives this end-to-end (record, select a session, play at up to 300×, loop); it's also reachable directly via the `/replay/*` endpoints in `agent_orchestration/main.py`.

## 6. Strategic Guardrails & Anti-Patterns

### What to Watch Out For (Gotchas)

* **LLMs Cannot Do Math:** Never let the LLM estimate a distance or threshold. Tower's runway-intersection check and the Phase 1 proximity-conflict trigger are both real PostGIS queries, not model inference.
* **Rate Limits:** OpenSky throttles aggressively. The poller uses a configurable interval (default 12s, not naively 10s) plus an active daily-credit-budget guard — see `data_pipeline/poller/`.
* **Strict Role Boundaries:** A reasoning node never fabricates an aircraft identity or acts on another role's aircraft. `main.py` passes the real ICAO24 explicitly into every seed message — a callsign alone isn't enough to safely bind to a database row (this was a real bug, not a hypothetical: an early dashboard trial had a role query and act on a completely unrelated aircraft because it had to guess).

### What is Important to Remember

* **Backend > heavy UI, but visibility matters:** The dashboard exists because watching raw curl/JSON output isn't a real way to evaluate agent behavior — but it stays a static, no-build-step, framework-free page specifically so it doesn't become the project's engineering focus. The work is still Docker container stability, LangGraph state transition logic, and audit trails.
* **Tool Binding over Hardcoding:** MCP tools are the only way reasoning nodes touch radar/rules data — no custom API glue in the main execution loop.
* **Audit trails are mandatory:** every handoff and every deterministic safety check writes a durable row to `coordination_events`, not just ephemeral LangGraph state.

### What NOT to Do

* **DO NOT build an ML anomaly detector:** Proximity conflicts are flagged by hard-coded PostGIS SQL triggers, not a trained model.
* **DO NOT train large models:** If fine-tuning happens, stay at 8B-parameter scale with quantization.
* **DO NOT include VFR/Helicopters in the commercial-IFR pipeline:** filtered out at ingest (`is_commercial_ifr`); GA is only ever in scope as Ground/Tower's traffic-type distinction (C172/PA28 vs. regional jets), not as pipeline noise.

## 7. Running It

```
docker compose up -d                                   # postgis + Airflow + OpenSky poller
docker compose --profile phase3 up -d --build mcp_server agent_orchestration
```

Then open **http://localhost:8000/dashboard/** for the live scenario dashboard, or drive it directly:

```
curl -X POST http://localhost:8000/scenarios/standard_departure/reset
curl -X POST http://localhost:8000/trigger/clearance/ksba01 -d '{"context": "..."}'
```

For recorded-day replay, label a window of already-polled traffic from the host (connects to PostGIS on the mapped `localhost:5432`, no container exec needed):

```
python data_pipeline/replay/record_session.py --label "morning arrivals" --last-minutes 30
```

then pick it from the dashboard's "Recorded Day" panel and hit Play, or drive it directly via `POST /replay/{session_id}/start`.

`docker compose --profile phase5 run --rm evaluation python score_episode.py --start <iso> --end <iso>` runs offline reward scoring — safety/throughput/efficiency only; the coordination component currently raises (see §2's known gap).
