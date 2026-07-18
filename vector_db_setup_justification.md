
1. Wake Turbulence Recategorization (RECAT)
   The "Large behind Heavy" rule is actually outdated at major airports. The FAA transitioned LAX and other high-volume airspace to RECAT (Wake Turbulence Recategorization) Phase I/II.

Instead of four simple weight classes, RECAT categorizes aircraft into a highly specific 6x6 (or larger) matrix based on wingspan, approach speed, and wake decay.

Category A: Airbus A380

Category B: Upper Heavy (e.g., Boeing 747-8, A350)

Category C: Lower Heavy (e.g., Boeing 767)

Category D: Non-Pairwise Heavy

Category E: Upper Large (e.g., Boeing 737, A320)

Category F: Lower Large (e.g., Embraer 175)

Why the RAG DB is necessary: If a United 737-800 (Cat E) is following a Delta 767 (Cat C), the separation is different than if it is following a Lufthansa 747-8 (Cat B). Your agent must query the PostGIS database to get the aircraft types, then query the RAG database’s RECAT matrix to determine if the minimum distance is 3, 4, or 5 nautical miles.

2. Simultaneous Close Parallel Approaches (The NTZ)
   LAX has two sets of parallel runways (24L/24R and 25L/25R). Because these runways are relatively close together, aircraft landing simultaneously are governed by strict FAA rules regarding the No Transgression Zone (NTZ).

The NTZ is an invisible 2,000-foot-wide boundary line directly between the two parallel approach paths.

Why the RAG DB is necessary: If strong crosswinds push a 24R arrival into the NTZ, the agent must immediately abort the parallel 24L arrival. The RAG database holds the exact protocol for this scenario, known as a Breakout. The agent must query the DB to retrieve the mandatory FAA breakout phraseology and altitude divergence rules (e.g., one aircraft must be instructed to climb immediately while turning, while the other is maintained level).

3. Departure Interval Timing (Time vs. Distance)
   Separation minimums are not just about physical distance; they are also about time, specifically on the ground.

When clearing aircraft for takeoff on the same runway, controllers must use time-based wake turbulence separation. If a Category B aircraft departs, the FAA rulebook dictates that a Category E aircraft cannot begin its takeoff roll for exactly 2 minutes (or 3 minutes behind a Category A).

Why the RAG DB is necessary: Your South Tower agent has an American Airlines A320 (Cat E) at the hold-short line for Runway 25R, and a British Airways A380 (Cat A) just lifted off. The agent must query the RAG database for the specific same-runway departure interval, realize it must wait 3 minutes, and issue a "Line up and wait" command instead of a "Cleared for takeoff" command.

Upgrading the Agent Workflow
To support this complexity, your query_faa_rules tool should accept complex, multi-parameter RAG searches. When your LangGraph agent encounters a conflict, its internal reasoning should look like this:

Agent Thought: "I have two aircraft arriving on parallel runways 24L and 24R. The leading aircraft on 24R is an Airbus A350. The trailing aircraft on 24L is a Boeing 737. I need to know the RECAT lateral separation minimums for this pair, and the NTZ breakout rules if the A350 deviates."

Tool Call: query_faa_rules(topics=["RECAT Phase II Category B leading Category E", "NTZ breakout lateral divergence minimums"])

This proves to a recruiter or defense contractor that you aren't just building a simple chatbot. You have built an agent capable of retrieving, cross-referencing, and applying highly technical, matrix-based regulatory doctrine in real-time.
