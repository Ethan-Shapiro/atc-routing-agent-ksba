"""Phase 2 stub — MCP protocol server exposing query_radar and query_faa_rules tools.

Not implemented yet. Planned shape (see vector_db_setup_justification.md and the project plan):
  - query_radar(flight_id): parameterized-SQL wrapper over aircraft_state_current /
    aircraft_state_history / anomaly_events (Phase 1's schema), filtered
    WHERE is_commercial_ifr = true.
  - query_faa_rules(topics: list[str]): FAISS vector search over the chunked FAA JO 7110.65
    RECAT/NTZ/departure-interval sections, one search per topic, returning top-k chunks with
    section citations.
"""

raise NotImplementedError("mcp_server is a Phase 2 placeholder — not implemented yet")
