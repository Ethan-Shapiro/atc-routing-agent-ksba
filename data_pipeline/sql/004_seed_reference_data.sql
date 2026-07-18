-- Reference data. Both tables are designed to be extended in place (no code redeploy needed).

-- Commercial-carrier ICAO callsign prefixes serving LAX. Non-exhaustive starter set covering
-- major US majors/low-cost carriers, common TBIT international carriers, and major cargo
-- operators — add rows here as unmatched commercial callsigns are observed in ingestion_audit_log.
INSERT INTO airline_designators (icao_prefix, airline_name) VALUES
    ('AAL', 'American Airlines'),
    ('DAL', 'Delta Air Lines'),
    ('UAL', 'United Airlines'),
    ('SWA', 'Southwest Airlines'),
    ('ASA', 'Alaska Airlines'),
    ('JBU', 'JetBlue Airways'),
    ('FFT', 'Frontier Airlines'),
    ('NKS', 'Spirit Airlines'),
    ('HAL', 'Hawaiian Airlines'),
    ('AAY', 'Allegiant Air'),
    ('SKW', 'SkyWest Airlines'),
    ('ACA', 'Air Canada'),
    ('VOI', 'Volaris'),
    ('AMX', 'Aeromexico'),
    ('BAW', 'British Airways'),
    ('DLH', 'Lufthansa'),
    ('AFR', 'Air France'),
    ('KLM', 'KLM Royal Dutch Airlines'),
    ('UAE', 'Emirates'),
    ('QFA', 'Qantas'),
    ('ANA', 'All Nippon Airways'),
    ('JAL', 'Japan Airlines'),
    ('CPA', 'Cathay Pacific'),
    ('SIA', 'Singapore Airlines'),
    ('EVA', 'EVA Air'),
    ('CCA', 'Air China'),
    ('CSN', 'China Southern Airlines'),
    ('KAL', 'Korean Air'),
    ('FDX', 'FedEx Express'),
    ('UPS', 'United Parcel Service'),
    ('GTI', 'Atlas Air')
ON CONFLICT (icao_prefix) DO NOTHING;

-- Coarse North/South tower jurisdiction boundaries, split at the approximate Taxiline Tango
-- dividing latitude (33.944) referenced in multi-agent_orchestration_rf_layer.md. These are
-- rectangular placeholders covering the LAX complex and immediate approach/departure corridors,
-- NOT precise runway-centerline-derived polygons — refine with real airport diagram data when
-- Phase 3 (ST_Contains-based north_aircraft/south_aircraft population) is implemented.
INSERT INTO runway_complex (complex_name, boundary) VALUES
    ('NORTH', ST_SetSRID(ST_GeomFromText(
        'POLYGON((-118.470 33.944, -118.370 33.944, -118.370 33.985, -118.470 33.985, -118.470 33.944))'
    ), 4326)::geography),
    ('SOUTH', ST_SetSRID(ST_GeomFromText(
        'POLYGON((-118.470 33.900, -118.370 33.900, -118.370 33.944, -118.470 33.944, -118.470 33.900))'
    ), 4326)::geography)
ON CONFLICT (complex_name) DO NOTHING;
