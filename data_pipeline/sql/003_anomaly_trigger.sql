-- Deterministic proximity-conflict detection. Row-level trigger, not a background job:
-- fires once per upserted aircraft, does one indexed GiST neighbor lookup against the
-- (small) current-aircraft set. Conversion constants: 1 NM = 1852 m, 1000 ft = 304.8 m.

CREATE OR REPLACE FUNCTION fn_flag_proximity_conflicts() RETURNS trigger AS $$
DECLARE
    other           RECORD;
    lateral_nm      numeric;
    vertical_ft     numeric;
BEGIN
    -- Resolve any open anomaly involving this aircraft whose partner no longer meets the
    -- conflict criteria (moved apart, landed, or vanished from aircraft_state_current).
    -- Aircraft that vanish entirely from the table are instead resolved by the retention DAG.
    UPDATE anomaly_events ae
    SET resolved_at = now(),
        resolution_notes = 'aircraft separated'
    WHERE ae.resolved_at IS NULL
      AND ae.anomaly_type = 'PROXIMITY_CONFLICT'
      AND (ae.aircraft_icao24_1 = NEW.icao24 OR ae.aircraft_icao24_2 = NEW.icao24)
      AND NOT EXISTS (
          SELECT 1
          FROM aircraft_state_current partner
          WHERE partner.icao24 = CASE WHEN ae.aircraft_icao24_1 = NEW.icao24
                                       THEN ae.aircraft_icao24_2
                                       ELSE ae.aircraft_icao24_1
                                  END
            AND NEW.on_ground = false
            AND partner.on_ground = false
            AND ST_DWithin(NEW.geom, partner.geom, 3 * 1852.0)
            AND ABS(NEW.baro_altitude_m - partner.baro_altitude_m) < 304.8
      );

    -- Conflicts are airborne-only; a grounded aircraft has nothing further to check.
    IF NEW.on_ground THEN
        RETURN NEW;
    END IF;

    FOR other IN
        SELECT c.*
        FROM aircraft_state_current c
        WHERE c.icao24 <> NEW.icao24
          AND c.on_ground = false
          AND ST_DWithin(NEW.geom, c.geom, 3 * 1852.0)
          AND ABS(NEW.baro_altitude_m - c.baro_altitude_m) < 304.8
    LOOP
        -- Skip if an open anomaly already exists for this exact pair, in either order.
        IF NOT EXISTS (
            SELECT 1 FROM anomaly_events ae
            WHERE ae.resolved_at IS NULL
              AND ae.anomaly_type = 'PROXIMITY_CONFLICT'
              AND ((ae.aircraft_icao24_1 = NEW.icao24 AND ae.aircraft_icao24_2 = other.icao24)
                OR (ae.aircraft_icao24_1 = other.icao24 AND ae.aircraft_icao24_2 = NEW.icao24))
        ) THEN
            lateral_nm  := ST_Distance(NEW.geom, other.geom) / 1852.0;
            vertical_ft := ABS(NEW.baro_altitude_m - other.baro_altitude_m) / 0.3048;

            INSERT INTO anomaly_events (
                anomaly_type, aircraft_icao24_1, aircraft_icao24_2,
                lateral_distance_nm, vertical_distance_ft, raw_context
            )
            VALUES (
                'PROXIMITY_CONFLICT', NEW.icao24, other.icao24,
                lateral_nm, vertical_ft,
                jsonb_build_object(
                    'aircraft_1', to_jsonb(NEW),
                    'aircraft_2', to_jsonb(other)
                )
            );
        END IF;
    END LOOP;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_flag_proximity_conflicts
    AFTER INSERT OR UPDATE OF geom, baro_altitude_m, on_ground ON aircraft_state_current
    FOR EACH ROW
    EXECUTE FUNCTION fn_flag_proximity_conflicts();
