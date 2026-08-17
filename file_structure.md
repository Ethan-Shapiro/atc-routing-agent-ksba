autonomous-atc-agent/
├── README.md                     # Project spec — KSBA, 4-role sequential handoff design
├── docker-compose.yml            # Orchestrates the microservices (profiles: phase2, phase3, phase5)
├── .env.example                  # API keys + domain constants (OpenSky, Anthropic, KSBA lat/lon)
│
├── data_pipeline/                # Phase 1: Geospatial & Telemetry
│   ├── dags/                     # Airflow housekeeping DAGs (retention, poller healthcheck)
│   ├── poller/                   # Always-on OpenSky polling service (not an Airflow DAG)
│   ├── sql/                      # PostGIS schema: aircraft state, anomaly triggers, per-runway
│   │                              # geometry (`runway` table — real KSBA 7/25 x 15/33 geometry)
│   └── requirements.txt
│
├── mcp_server/                   # Phase 2: Tooling & RAG
│   ├── vector_db/                # FAISS index over JO 7110.65 / JO 7360.1
│   ├── tools/                    # query_radar.py, query_faa_rules.py
│   ├── source_docs/               # Ingested FAA PDFs
│   ├── server.py                 # The MCP protocol server loop (SSE transport)
│   └── requirements.txt
│
├── agent_orchestration/          # Phase 3/4: LangGraph, 4-role sequential handoff
│   ├── state.py                  # MultiRoleATCState schema (aircraft_by_role, handoff_buffer)
│   ├── roles.py                  # The 4 role identities: frequency, facility name
│   ├── scenarios.py               # Demo scenario catalog (seed data + chain steps) for the dashboard
│   ├── nodes/                    # reasoning_engine (4 role prompts), handoff_handler,
│   │                              # observation_builder (phase-of-flight classifier),
│   │                              # phraseology_generator
│   ├── graph.py                  # The routing logic and edges
│   └── main.py                   # FastAPI: anomaly + manual role triggers, dashboard static mount
│
├── dashboard/                    # Live scenario-playback UI (plain HTML/CSS/JS, no build step)
│   ├── index.html
│   ├── app.js                    # Fetches /airport/layout + /scenarios, drives playback
│   └── style.css
│
├── evaluation/                   # Phase 5: offline reward scoring (replay, not live RL training)
│   ├── reward.py                 # R_t = safety + throughput + efficiency + coordination
│   └── score_episode.py
│
├── ksba_prototype/                # Standalone judgment-quality sandbox (plain Anthropic SDK,
│   │                              # no MCP/PostGIS) — validated the 4-role system prompts before
│   │                              # the real integrated system above was built. Kept as reference.
│   ├── agents.py                 # The 4 role system prompts (source for agent_orchestration's)
│   ├── scenarios.py / chains.py  # Isolated scenarios + full handoff-chain test harness
│   └── run_scenarios.py / run_chain.py
│
└── model_finetuning/             # Phase 4 (deferred, not currently used — see README §4)
    ├── raw_audio/                # Ignored by git
    ├── transcripts/              # Whisper outputs / corrected JSONL
    ├── scripts/                  # PyTorch training loop
    └── model_weights/            # LoRA adapters
