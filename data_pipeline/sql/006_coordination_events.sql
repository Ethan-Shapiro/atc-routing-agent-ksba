-- Phase 5 dependency: Phase 3's handoff_buffer is ephemeral LangGraph state (lives only for
-- the duration of one graph run), so the reward function's R_coordination term would have
-- nothing to read without a durable record. inter_agent_comm_handler writes one row here for
-- every role-to-role handoff in the Clearance -> Ground -> Tower -> Approach chain, and for
-- Tower's deterministic runway-intersection safety check specifically — this table is the
-- durable audit trail for both.
--
-- NOTE (post-KSBA-rescope): origin_agent/target_agent now hold role names
-- ('CLEARANCE'|'GROUND'|'TOWER'|'APPROACH') rather than the old 'NORTH_TOWER'/'SOUTH_TOWER'
-- values. evaluation/reward.py's compute_coordination_reward() still queries the retired
-- runway_complex table and needs a semantic rework (Stage 3, not yet done) before this
-- table's data is scored again.

CREATE TABLE coordination_events (
    id                      bigserial PRIMARY KEY,
    origin_agent            text NOT NULL,   -- 'CLEARANCE' | 'GROUND' | 'TOWER' | 'APPROACH'
    target_agent            text NOT NULL,
    aircraft_icao24         text NOT NULL,
    action_type             text NOT NULL,   -- 'ROLE_HANDOFF' | 'INTERSECTION_CONFLICT_CHECK'
    coordinate_threshold    geography(Point, 4326) NOT NULL,
    estimated_time_crossing timestamptz,
    reason                  text,
    coordination_status     text NOT NULL,   -- 'ACK' | 'COUNTER_PROPOSAL'
    verification_note       text,
    requested_at            timestamptz NOT NULL,
    resolved_at             timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_coordination_events_aircraft ON coordination_events (aircraft_icao24, resolved_at);
