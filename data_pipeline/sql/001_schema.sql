-- Autonomous ATC Routing Agent (Santa Barbara Municipal — KSBA) — Phase 1 domain schema
-- Applied automatically on first PostGIS container init (mounted into /docker-entrypoint-initdb.d)

CREATE EXTENSION IF NOT EXISTS postgis;

-- One row per aircraft currently believed to be in coverage, upserted every poll cycle.
CREATE TABLE aircraft_state_current (
    icao24              text PRIMARY KEY,
    callsign            text,
    origin_country      text,
    longitude           double precision NOT NULL,
    latitude            double precision NOT NULL,
    geom                geography(Point, 4326)
                            GENERATED ALWAYS AS (
                                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
                            ) STORED,
    -- Native OpenSky units (meters, m/s). Converted to NM/ft only at query time.
    baro_altitude_m     real,
    geo_altitude_m      real,
    on_ground           boolean NOT NULL DEFAULT false,
    velocity_mps        real,
    true_track_deg      real,
    vertical_rate_mps   real,
    squawk              text,
    spi                 boolean,
    position_source     smallint,
    category            smallint,
    -- RECAT wake category — no source for this in OpenSky's state vector; populated by Phase 2
    -- once an aircraft-type lookup exists. Column added now to avoid a Phase 2 migration.
    wake_category       text,
    is_commercial_ifr   boolean NOT NULL DEFAULT false,
    time_position       timestamptz,
    last_contact        timestamptz NOT NULL,
    ingested_at         timestamptz NOT NULL DEFAULT now()
);

-- Append-only time series: audit trail + Phase 5 reward/eval replay input.
CREATE TABLE aircraft_state_history (
    id                  bigserial PRIMARY KEY,
    icao24              text NOT NULL,
    callsign            text,
    origin_country      text,
    longitude           double precision NOT NULL,
    latitude            double precision NOT NULL,
    geom                geography(Point, 4326)
                            GENERATED ALWAYS AS (
                                ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography
                            ) STORED,
    baro_altitude_m     real,
    geo_altitude_m      real,
    on_ground           boolean NOT NULL DEFAULT false,
    velocity_mps        real,
    true_track_deg      real,
    vertical_rate_mps   real,
    squawk              text,
    spi                 boolean,
    position_source     smallint,
    category            smallint,
    wake_category       text,
    is_commercial_ifr   boolean NOT NULL DEFAULT false,
    time_position       timestamptz,
    last_contact        timestamptz NOT NULL,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    -- Dedupes back-to-back polls that return an unchanged underlying broadcast.
    UNIQUE (icao24, time_position)
);

-- Audit trail for every flagged conflict (Phase 1: PROXIMITY_CONFLICT only).
CREATE TABLE anomaly_events (
    id                      bigserial PRIMARY KEY,
    anomaly_type            text NOT NULL,  -- 'PROXIMITY_CONFLICT' now; 'NTZ_INCURSION'/'WEATHER_CELL' reserved
    aircraft_icao24_1       text NOT NULL,
    aircraft_icao24_2       text,           -- nullable for future single-aircraft anomaly types
    lateral_distance_nm     numeric,
    vertical_distance_ft    numeric,
    detected_at             timestamptz NOT NULL DEFAULT now(),
    resolved_at             timestamptz,
    resolution_notes        text,
    -- Full snapshot of both aircraft's state at detection time, so the audit trail is
    -- self-contained even after aircraft_state_current has moved on.
    raw_context             jsonb NOT NULL
);

-- Durable, queryable record of every poll cycle (beyond ephemeral container logs).
CREATE TABLE ingestion_audit_log (
    id                          bigserial PRIMARY KEY,
    poll_started_at             timestamptz NOT NULL,
    poll_completed_at           timestamptz,
    http_status                 int,
    aircraft_count_returned     int,
    aircraft_count_upserted     int,
    credits_used_estimate       int,
    error                       text
);

-- Reference table for the commercial-callsign filter. Extendable without redeploying poller code.
CREATE TABLE airline_designators (
    icao_prefix     text PRIMARY KEY,
    airline_name    text NOT NULL
);

-- Per-runway geometry for KSBA. Replaces the old LAX-era coarse North/South jurisdiction
-- rectangles: KSBA has a single physical runway intersection (7/25 crossed by the 15/33
-- pair) that the Tower agent's separation rule depends on directly, so precise per-runway
-- threshold/centerline data is both meaningful and tractable at this airport's scale.
CREATE TABLE runway (
    runway_id       text PRIMARY KEY,        -- '7' | '25' | '15L' | '33R' | '15R' | '33L'
    heading_deg     numeric NOT NULL,
    threshold_geom  geography(Point, 4326) NOT NULL,
    centerline_geom geography(LineString, 4326) NOT NULL,
    -- Other runway_ids that physically cross this one (e.g. '25' lists the 15/33 pair).
    -- Drives Tower's intersection safety check — see nodes/inter_agent_comm_handler.py.
    intersects_with text[] NOT NULL DEFAULT '{}'
);
