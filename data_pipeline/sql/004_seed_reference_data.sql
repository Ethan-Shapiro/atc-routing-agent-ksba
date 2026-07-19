-- Reference data. Both tables are designed to be extended in place (no code redeploy needed).

-- Commercial-carrier ICAO callsign prefixes. KSBA's actual scheduled service is a small
-- subset of this list (SkyWest/United Express, American Eagle, Alaska) — the rest is kept as
-- a general-purpose reference set (majors, low-cost carriers, cargo) so occasional diverted/
-- charter/repositioning traffic through the bbox still matches without a redeploy. Add rows
-- here as unmatched commercial callsigns are observed in ingestion_audit_log.
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

-- KSBA runway geometry: primary Runway 7/25 (~6,050 ft) crossed by the shorter 15/33 pair
-- (15L/33R ~4,180 ft, 15R/33L ~2,890 ft). Coordinates below are approximate — derived from
-- the airport reference point (34.4262, -119.8415) plus published headings/lengths, NOT
-- surveyed from an official airport diagram. Same "close enough for a portfolio project"
-- precision bar the old LAX placeholder rectangles used; refine if real diagram data
-- becomes available.
INSERT INTO runway (runway_id, heading_deg, threshold_geom, centerline_geom, intersects_with) VALUES
    ('7', 70,
        ST_SetSRID(ST_MakePoint(-119.8510, 34.4234), 4326)::geography,
        ST_SetSRID(ST_MakeLine(ST_MakePoint(-119.8510, 34.4234), ST_MakePoint(-119.8321, 34.4290)), 4326)::geography,
        '{15L,33R,15R,33L}'),
    ('25', 250,
        ST_SetSRID(ST_MakePoint(-119.8321, 34.4290), 4326)::geography,
        ST_SetSRID(ST_MakeLine(ST_MakePoint(-119.8510, 34.4234), ST_MakePoint(-119.8321, 34.4290)), 4326)::geography,
        '{15L,33R,15R,33L}'),
    ('15L', 150,
        ST_SetSRID(ST_MakePoint(-119.8450, 34.4312), 4326)::geography,
        ST_SetSRID(ST_MakeLine(ST_MakePoint(-119.8450, 34.4312), ST_MakePoint(-119.8380, 34.4212)), 4326)::geography,
        '{7,25}'),
    ('33R', 330,
        ST_SetSRID(ST_MakePoint(-119.8380, 34.4212), 4326)::geography,
        ST_SetSRID(ST_MakeLine(ST_MakePoint(-119.8450, 34.4312), ST_MakePoint(-119.8380, 34.4212)), 4326)::geography,
        '{7,25}'),
    ('15R', 150,
        ST_SetSRID(ST_MakePoint(-119.8396, 34.4305), 4326)::geography,
        ST_SetSRID(ST_MakeLine(ST_MakePoint(-119.8396, 34.4305), ST_MakePoint(-119.8353, 34.4230)), 4326)::geography,
        '{7,25}'),
    ('33L', 330,
        ST_SetSRID(ST_MakePoint(-119.8353, 34.4230), 4326)::geography,
        ST_SetSRID(ST_MakeLine(ST_MakePoint(-119.8396, 34.4305), ST_MakePoint(-119.8353, 34.4230)), 4326)::geography,
        '{7,25}')
ON CONFLICT (runway_id) DO NOTHING;
