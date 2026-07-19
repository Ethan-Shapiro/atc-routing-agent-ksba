-- Phase 5 dependency: Phase 3's inter_agent_buffer is ephemeral LangGraph state (lives only
-- for the duration of one graph run), so the reward function's R_coordination term
-- (N_uncoordinated_crossings, N_deadlocks) would have nothing to read without a durable
-- record of what got coordinated. inter_agent_comm_handler writes one row here per resolved
-- request — this table IS the audit trail multi-agent_orchestration_rf_layer.md's JSON
-- contract describes, just persisted instead of living only in memory.

CREATE TABLE coordination_events (
    id                      bigserial PRIMARY KEY,
    origin_agent            text NOT NULL,   -- 'NORTH_TOWER' | 'SOUTH_TOWER'
    target_agent            text NOT NULL,
    aircraft_icao24         text NOT NULL,
    action_type             text NOT NULL,   -- e.g. 'TAXI_CROSSING'
    coordinate_threshold    geography(Point, 4326) NOT NULL,
    estimated_time_crossing timestamptz,
    reason                  text,
    coordination_status     text NOT NULL,   -- 'ACK' | 'COUNTER_PROPOSAL'
    verification_note       text,
    requested_at            timestamptz NOT NULL,
    resolved_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_coordination_events_aircraft ON coordination_events (aircraft_icao24, resolved_at);
