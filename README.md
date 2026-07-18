# Project Specification: Autonomous ATC Routing Agent (LAX Sector)

## 1. Project Objective & Core Concept

**The Concept:** An autonomous Multi-Agent Reinforcement Learning (MARL) system that monitors live flight telemetry around Los Angeles International Airport (LAX). When a deterministic anomaly occurs (e.g., airspace encroachment, weather cell), the agents formulate safe, coordinated rerouting instructions using standard aviation phraseology.
**The Problem:** Human controllers face extreme cognitive load during airspace disruptions. This acts as an AI "co-pilot," synthesizing radar data, weather, and complex FAA regulations into actionable commands instantly.
**Defense & Aerospace Alignment:** This architecture directly mirrors autonomous systems operating in physical space (e.g., routing drone swarms around radar threats). It is designed as a portfolio cornerstone for advanced ML/Data Science roles within the defense and aerospace sectors, proving capability in distributed agent orchestration, deterministic safety guardrails, and real-time geospatial processing.

## 2. The Tech Stack

* **AI/ML Core:** PyTorch, Hugging Face (QLoRA), Llama 3 (8B).
* **Agentic Framework:** LangGraph (Multi-Agent StateGraph for North/South tower coordination).
* **Interoperability:** Model Context Protocol (MCP) Python SDK.
* **Data & Geospatial:** PostgreSQL with PostGIS extension, FAISS or Milvus (Vector Database).
* **Infrastructure:** Apache Airflow (data ingestion), Docker & Docker Compose (microservices).

## 3. The Data Sources (Free/Public)

* **Live Telemetry:** OpenSky Network API. Data will be strictly filtered to ingest **only commercial IFR (Instrument Flight Rules) flights**, ignoring VFR and helicopter traffic to ensure predictable state transitions.
* **The Rulebook (RAG):** FAA Order JO 7110.65 (Air Traffic Control). The Vector DB must be structured to accurately retrieve complex matrix tables, specifically:
  * *Wake Turbulence Recategorization (RECAT) Phase I/II* (6x6 matrix minimums).
  * *Simultaneous Close Parallel Approaches (NTZ Breakout rules).*
  * *Time-based departure intervals.*
* **Fine-Tuning Data:** LiveATC.net archives. Isolated LAX feeds for Ground, Tower, and Final Approach. (e.g., North Tower 133.900, South Tower 119.800). No combined feeds.

---

## 4. Architectural Blueprint

### Phase 1: The Geospatial Pipeline (PostGIS + Airflow)

Set up a PostGIS database. Write an Airflow DAG that polls the OpenSky API every 10 seconds for commercial flights within 50 miles of LAX. Upsert this data into PostGIS. Write deterministic SQL triggers to flag anomalies (e.g., `ST_Distance(plane1.geom, plane2.geom) < 3 nautical miles`).

### Phase 2: RAG & MCP Server Configuration

Chunk the FAA ATC rulebook (focusing on RECAT and NTZ matrices) into FAISS/Milvus. Use the Python `mcp` library to build a server exposing two primary tools to the LLMs:

1. `query_radar(flight_id)`: Executes PostGIS spatial SQL to return precise aircraft vectors and wake categories.
2. `query_faa_rules(topics)`: Executes a vector search to return specific separation minimums.

### Phase 3: The Multi-Agent LangGraph Orchestration

LAX operates as two parallel airports. The graph must implement a Centralized Training, Decentralized Execution (CTDE) architecture using two primary actors:

* **North Tower Agent:** Manages Runways 24L/R, 6L/R, and northern taxiways.
* **South Tower Agent:** Manages Runways 25L/R, 7L/R, and southern taxiways.
* **The Routing Logic:** Agents operate in parallel on their own isolated state vectors. A conditional router checks if an action impacts the opposite complex. If so, a structured JSON contract is passed through an `inter_agent_buffer` (a shared state scratchpad) requiring an `ACK` from the opposing agent before phraseology is generated.

### Phase 4: Domain-Specific Fine-Tuning (The ML Gap)

Use PyTorch and QLoRA to fine-tune Llama 3 (8B) on the LiveATC transcripts. The objective is to override conversational defaults and force the model to output strict, unambiguous FAA phraseology (e.g., "Delta 123, LAX Tower, turn left heading 2-4-0, maintain 5,000 feet").

### Phase 5: The Evaluation Reward Function

Implement a mathematical reward function to evaluate agent performance, prioritizing safety above all else:

$$
R_t = w_s R_{\text{safety}} + w_t R_{\text{throughput}} + w_e R_{\text{efficiency}} + w_c R_{\text{coordination}}
$$

Where $R_{\text{safety}}$ relies on a continuous exponential decay function that heavily penalizes agents as lateral distances drop below 3.0 NM or vertical distances drop below 1000 ft, modified by the specific RECAT wake turbulence category.

---

## 5. Strategic Guardrails & Anti-Patterns

### What to Watch Out For (Gotchas)

* **LLMs Cannot Do Math:** Language models fail at spatial geometry. Do not let the LLM guess a 30-degree offset. Provide a Python spatial calculator tool or PostGIS function to do the arithmetic.
* **Rate Limits:** OpenSky will IP ban for polling faster than every 10 seconds. Implement strict throttling in the Airflow DAG.
* **Strict Agent Boundaries:** Never allow the North Agent to directly issue a command to a South Agent aircraft. All cross-boundary operations must go through the `inter_agent_buffer`.

### What is Important to Remember

* **Backend > UI:** Do not build a React frontend. Engineering focus must remain entirely on Docker container stability, LangGraph state transition logic, pipeline latency, and rigorous logging (audit trails are mandatory in aerospace).
* **Tool Binding over Hardcoding:** Use MCP properly to prove knowledge of standardized, secure tool integrations, rather than writing custom API glue code in the main execution loop.

### What NOT to Do

* **DO NOT build an ML anomaly detector:** Stick to hard-coded PostGIS SQL rules to trigger the agent. Training a separate model just to detect the anomalies dilutes the project's focus on Agentic AI.
* **DO NOT train large models:** Stick to 8B parameter models with quantization (QLoRA). Do not attempt to load a 70B model locally.
* **DO NOT include VFR/Helicopters:** Exclude all general aviation. Mixed traffic introduces unpredictable state transitions that will prevent the agents from converging on optimal policies.
