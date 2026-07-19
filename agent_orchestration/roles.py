"""KSBA's 4 ATC role identities: static facts (frequency, spoken facility name), not
queryable operational data — kept as Python constants rather than a DB table. Values
validated in ksba_prototype/agents.py's system prompts before this rearchitecture.

Ordering matters for ROLE_NAMES: it's the default departure-direction chain
(Clearance -> Ground -> Tower -> Approach). Arrivals run the reverse direction
(Approach -> Tower -> Ground) — reasoning nodes hand off to an explicit target_role
rather than an implicit "next" lookup, so both directions work without special-casing.
"""

ROLE_NAMES = ("CLEARANCE", "GROUND", "TOWER", "APPROACH")

ROLE_FREQUENCY_MHZ = {
    "CLEARANCE": "132.9",
    "GROUND": "121.7",
    "TOWER": "119.7",
    "APPROACH": "120.55",
}

# Spoken facility name, e.g. "Santa Barbara Ground" — used both in system prompts and by
# the phraseology template formatter.
ROLE_FACILITY_NAME = {
    "CLEARANCE": "Santa Barbara Clearance",
    "GROUND": "Santa Barbara Ground",
    "TOWER": "Santa Barbara Tower",
    "APPROACH": "Santa Barbara Approach",
}
