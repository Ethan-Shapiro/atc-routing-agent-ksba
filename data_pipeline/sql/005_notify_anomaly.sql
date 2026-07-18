-- Phase 3 wiring: notify agent_orchestration of new anomalies via LISTEN/NOTIFY instead of
-- polling or standing up a message queue — consistent with the project's own "no infra
-- sprawl" philosophy. Payload is a compact JSON summary; agent_orchestration re-queries
-- anomaly_events for the full row rather than trusting the NOTIFY payload as authoritative.

CREATE OR REPLACE FUNCTION fn_notify_new_anomaly() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify(
        'new_anomaly',
        jsonb_build_object(
            'id', NEW.id,
            'anomaly_type', NEW.anomaly_type,
            'aircraft_icao24_1', NEW.aircraft_icao24_1,
            'aircraft_icao24_2', NEW.aircraft_icao24_2
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_notify_new_anomaly
    AFTER INSERT ON anomaly_events
    FOR EACH ROW
    EXECUTE FUNCTION fn_notify_new_anomaly();
