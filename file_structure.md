autonomous-atc-agent/
├── README.md                     # Paste the entire project spec here
├── docker-compose.yml            # Orchestrates the microservices
├── .env.example                  # API keys (OpenSky, HuggingFace)
│
├── data_pipeline/                # Phase 1: Geospatial & Telemetry
│   ├── dags/                     # Airflow DAGs for OpenSky polling
│   ├── sql/                      # PostGIS schema and anomaly triggers
│   └── requirements.txt
│
├── mcp_server/                   # Phase 2: Tooling & RAG
│   ├── vector_db/                # FAISS/Milvus instantiation
│   ├── tools/                    # query_radar.py, query_faa_rules.py
│   ├── server.py                 # The MCP protocol server loop
│   └── requirements.txt
│
├── agent_orchestration/          # Phase 3: LangGraph
│   ├── state.py                  # MultiAgentATCState schema
│   ├── nodes/                    # reasoning_engine, tool_executor
│   ├── graph.py                  # The routing logic and edges
│   └── main.py                   # FastAPI endpoint to trigger graph
│
└── model_finetuning/             # Phase 4: QLoRA & Audio
    ├── raw_audio/                # Ignored by git
    ├── transcripts/              # Whisper outputs / corrected JSONL
    ├── scripts/                  # PyTorch training loop
    └── model_weights/            # LoRA adapters
