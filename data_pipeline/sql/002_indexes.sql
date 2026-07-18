-- Spatial and lookup indexes for Phase 1 tables.

CREATE INDEX idx_aircraft_state_current_geom ON aircraft_state_current USING GIST (geom);
CREATE INDEX idx_aircraft_state_current_ifr ON aircraft_state_current (is_commercial_ifr);

CREATE INDEX idx_aircraft_state_history_geom ON aircraft_state_history USING GIST (geom);
CREATE INDEX idx_aircraft_state_history_icao_time ON aircraft_state_history (icao24, time_position);

CREATE INDEX idx_anomaly_events_detected_at ON anomaly_events (detected_at);

-- This partial index is exactly the query behind Phase 3's `active_anomalies` state field.
CREATE INDEX idx_anomaly_events_open_pairs ON anomaly_events (aircraft_icao24_1, aircraft_icao24_2)
    WHERE resolved_at IS NULL;

CREATE INDEX idx_runway_complex_boundary ON runway_complex USING GIST (boundary);
