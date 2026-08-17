
This document justifies why `query_faa_rules` is a real vector-search tool over FAA Order JO 7110.65 / JO 7360.1, not a lookup table or a set of hardcoded rules embedded in a system prompt. The corpus is national FAA doctrine — not airport-specific — so everything below applies regardless of which airport this project targets; only the last section describes what's actually wired into the current KSBA implementation.

1. Wake Turbulence Recategorization (RECAT)
   The "Large behind Heavy" rule is outdated at most towered airports today. The FAA transitioned to RECAT (Wake Turbulence Recategorization) Phase I/II nationally.

Instead of four simple weight classes, RECAT categorizes aircraft into a highly specific 6x6 (or larger) matrix based on wingspan, approach speed, and wake decay.

Category A: Airbus A380

Category B: Upper Heavy (e.g., Boeing 747-8, A350)

Category C: Lower Heavy (e.g., Boeing 767)

Category D: Non-Pairwise Heavy

Category E: Upper Large (e.g., Boeing 737, A320)

Category F: Lower Large (e.g., Embraer 175, CRJ700 — KSBA's actual regional-jet traffic)

Why the RAG DB is necessary: If a SkyWest CRJ700 (Cat F) is following a heavier jet, the separation is different than if it's following a smaller GA aircraft. The agent must query PostGIS for aircraft types, then query the RAG database's RECAT matrix to determine the minimum distance. (Note: `wake_category` is currently unpopulated in this project's schema — see README §2's status table and `evaluation/reward.py`'s documented `P_wake` gap — so this specific lookup isn't yet exercised end-to-end, but the tool and corpus support it.)

2. Simultaneous Close Parallel Approaches (The NTZ)
   Large airports with closely-spaced parallel runways (LAX's 24L/24R and 25L/25R, for example) are governed by strict FAA rules regarding the No Transgression Zone (NTZ) — an invisible 2,000-foot-wide boundary directly between two parallel approach paths.

Why the RAG DB is necessary: If strong crosswinds push one parallel arrival into the NTZ, the agent must immediately abort the adjacent parallel arrival. The RAG database holds the exact protocol for this scenario, known as a Breakout — mandatory phraseology and altitude-divergence rules (one aircraft climbs immediately while turning, the other is maintained level). This is a real, retrievable part of the ingested JO 7110.65 corpus, though it isn't a rule KSBA's Tower role currently needs — KSBA has no parallel-runway configuration. Kept here as an illustration of the corpus's depth, and because a future airport target could need it without any re-ingestion.

3. Departure Interval Timing (Time vs. Distance)
   Separation minimums are not just about physical distance; they are also about time, specifically on the ground.

When clearing aircraft for takeoff on the same runway, controllers must use time-based wake turbulence separation. If a Category B aircraft departs, the FAA rulebook dictates that a Category E aircraft cannot begin its takeoff roll for exactly 2 minutes (or 3 minutes behind a Category A).

Why the RAG DB is necessary: Tower has a regional jet at the hold-short line for Runway 7, and a heavier aircraft just lifted off. The agent must query the RAG database for the specific same-runway departure interval, realize it must wait, and issue a "Line up and wait" command instead of "Cleared for takeoff."

Upgrading the Agent Workflow
`query_faa_rules` accepts complex, multi-parameter RAG searches — a caller can pass multiple topics in one call, each independently searched and deduplicated by result chunk. When the LangGraph agent encounters a conflict, its internal reasoning should look like this:

Agent Thought: "I have two aircraft arriving on parallel runways 24L and 24R. The leading aircraft on 24R is an Airbus A350. The trailing aircraft on 24L is a Boeing 737. I need to know the RECAT lateral separation minimums for this pair, and the NTZ breakout rules if the A350 deviates."

Tool Call: `query_faa_rules(topics=["RECAT Phase II Category B leading Category E", "NTZ breakout lateral divergence minimums"])`

This proves the agent isn't just a chatbot with a prompt full of hardcoded rules — it retrieves, cross-references, and applies genuine matrix-based regulatory doctrine at decision time.

4. What's actually wired into the KSBA implementation today

The rule that's real, implemented, and live-verified in this project is Tower's **runway-intersection safety check**: Runways 15L/15R/33L/33R physically cross Runway 7/25 at KSBA (real geometry in `data_pipeline/sql`'s `runway` table), and Tower must never clear 15/33 traffic if a commercial aircraft is within 2 NM of the 7/25 threshold on final. The distance check itself is **not** answered by `query_faa_rules` — per this project's "LLMs cannot do math" guardrail, that's a deterministic PostGIS query (`nodes/handoff_handler.py`'s `check_runway_conflict` path), same reasoning as the RECAT/NTZ examples above: don't let the model estimate a number it can query exactly. Where `query_faa_rules` *does* get used for this rule is confirming exact FAA phraseology (e.g. "Cleared for takeoff" vs. "Hold short" wording, or intersection-departure procedure) — the corpus answers "what do I say and under what named procedure," the PostGIS query answers "is there actually a conflict right now."

Agent Thought (as actually implemented): "I have a GA aircraft holding short of Runway 15R, ready to depart. 15R intersects Runway 7/25. Before I can clear it, I need to verify no commercial traffic is within 2 NM of an intersecting runway's threshold — that's a deterministic check, not something I should estimate."

Tool Call: `check_runway_conflict(aircraft_id="...", runway_id="15R")` — real PostGIS query, not RAG. `query_faa_rules` is reserved for phraseology/procedure confirmation, not the safety math itself.
