-- Stage 1 of the recorded-day replay feature (see README / plan). A "recording session" is a
-- named [started_at, ended_at) window over aircraft_state_history — the append-only track log
-- the poller already writes. The replay driver (agent_orchestration/replay.py) plays that
-- window back on a clock, re-populating aircraft_state_current so the real pipeline (proximity
-- trigger + pg_notify + agent) fires against real traffic.

CREATE TABLE recording_session (
    id          bigserial PRIMARY KEY,
    label       text NOT NULL,
    started_at  timestamptz NOT NULL,
    ended_at    timestamptz NOT NULL,
    notes       text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Caches every agent decision made during a replay so a re-loop is a cache hit, never a
-- re-ping of the Anthropic API (the whole point of "loop over the day without pinging the
-- API"). event_fingerprint is content-derived and stable across loops (aircraft + role +
-- trigger kind + a coarse replay-time bucket), so the same event resolves to the same row
-- every loop. replay_time is the replay-clock instant the event fired — the timeline in the
-- dashboard is ordered by it (created_at is wall-clock and differs per loop, so it can't be).
CREATE TABLE replay_agent_response (
    id                  bigserial PRIMARY KEY,
    session_id          bigint NOT NULL REFERENCES recording_session(id) ON DELETE CASCADE,
    icao24              text NOT NULL,
    callsign            text,
    role                text NOT NULL,
    trigger_kind        text NOT NULL,   -- 'ROLE_TRANSITION' | 'ANOMALY'
    event_fingerprint   text NOT NULL,
    replay_time         timestamptz NOT NULL,
    final_message       text,
    tool_calls          jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (session_id, event_fingerprint)
);

CREATE INDEX idx_replay_agent_response_session_time
    ON replay_agent_response (session_id, replay_time);
