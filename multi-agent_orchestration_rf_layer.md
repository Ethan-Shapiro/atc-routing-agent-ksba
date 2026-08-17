
This specification defines the architectural blueprint and mathematical framework for the **Multi-Role Orchestration and Reward** layer of the Autonomous ATC Routing Agent for Santa Barbara Municipal (KSBA).

By splitting the ATC workflow into the 4 real controller roles — **Clearance Delivery**, **Ground**, **Tower**, and **Approach** — we model phase-of-flight as the jurisdictional boundary (not geography, as the project's earlier LAX North/South design did), with an explicit, directional handoff channel as an aircraft moves between roles.

Implementation note: this document describes the design as actually built in `agent_orchestration/`. Where the code and this document could drift (they will, over time), the code is authoritative — this file exists to explain *why* the code is shaped the way it is.

---

## 1. Multi-Role System (MRS) Formalization

The KSBA environment is modeled as a sequential hand-off chain rather than a Markov game between simultaneous peers: at any moment, exactly one role holds "execution focus" for a given aircraft, and that focus moves forward through the chain as the aircraft's phase of flight changes.

### Role Jurisdictions

* **Clearance Delivery** (132.9): Issues CRAFT-format IFR clearances before pushback. Structurally pre-radar — an aircraft awaiting clearance typically isn't broadcasting ADS-B yet, so this role can't be reached by live telemetry-based triggering (see §2's note on `active_role`).
* **Ground** (121.7): Routes aircraft from parking to their departure runway threshold. Commercial jets → Runway 7/25; GA aircraft → Runway 15L/15R. Enforces hold-short instructions and demands pilot readback.
* **Tower** (119.7): Sequences takeoffs and landings. Runways 15L/15R/33L/33R physically intersect 7/25 (real geometry in `data_pipeline/sql`'s `runway` table, via each row's `intersects_with` array) — Tower must never clear 15/33 traffic if a commercial aircraft is within 2 NM of the 7/25 threshold on final.
* **Approach** (120.55): Sequences inbound IFR traffic onto final and accepts outbound aircraft on climb-out check-in. Speed control is the primary separation tool, ahead of vectoring/altitude changes.

Departures flow **Clearance → Ground → Tower → Approach**. Arrivals flow **Approach → Tower → Ground**. There is no fixed "next role" table in the implementation — a role hands off to an **explicit** target role it names itself, which is what makes both directions work through the same 4 reasoning nodes without special-casing.

### State, Observation, and Action Spaces

```text
+-----------------------------------------------------------------------+
|                         GLOBAL ENVIRONMENT (PostGIS)                  |
|  Aircraft Telemetry [X, Y, Z, V, psi] | Runway Geometry | Anomalies   |
+-----------------------------------------------------------------------+
                                    |
                    Phase-of-flight classifier
              (on_ground / altitude / velocity / distance
                     from the airport reference point)
                                    |
         +----------+----------+----------+----------+
         | CLEARANCE|  GROUND  |  TOWER   | APPROACH |
         | (manual  |          |          |          |
         |  trigger |          |          |          |
         |  only)   |          |          |          |
         +----------+----------+----------+----------+
              \_________ explicit advance_to_next_role _________/
```

#### Observation Space ($o_t$)

Each role's reasoning node receives, per turn:

* **Aircraft in its jurisdiction:** `state["aircraft_by_role"][role_name]` — populated by `nodes/observation_builder.py`'s deterministic phase-of-flight classifier, not a spatial region membership check. CLEARANCE is never populated here (pre-radar), reachable only via the manual trigger path.
* **Active anomalies:** open `PROXIMITY_CONFLICT` rows — visible to all roles, most directly actionable by Tower.
* **Pending handoff/conflict-check status:** `state["handoff_buffer"]`, described in §3.
* **Live tool access, not a snapshot:** every role is bound to `query_radar` (live PostGIS aircraft state) and `query_faa_rules` (FAISS search over JO 7110.65 / JO 7360.1) — the observation above is a hint, not the sole source of truth; the model is expected to call these tools rather than trust only what's embedded in the prompt.

#### Action Space ($a_t$)

Unlike the original LAX design's closed 3-type numeric action space (heading/altitude/speed only — sized for airborne conflict-avoidance), the KSBA 4-role chain needs a much wider instruction vocabulary (CRAFT clearance elements, taxi/hold-short instructions, takeoff/landing clearances, frequency handoffs). The action space is therefore a **free-form structured schema**, not a closed enum:

```python
class InstructionComponent(BaseModel):
    command_type: str   # e.g. CLEARANCE_LIMIT, ROUTE, ALTITUDE, RUNWAY_ASSIGNMENT,
                         # HOLD_SHORT, TAKEOFF_CLEARANCE, CONTACT_FREQUENCY,
                         # HEADING_CHANGE, ALTITUDE_CHANGE, SPEED_CHANGE, ...
    value: str           # the instruction detail, e.g. "KLAX", "120.55", "Runway 7, cleared for takeoff"
```

A role calls `finalize_instruction` with one or more of these components once it has decided what to instruct — it never writes the radio phraseology itself. A separate deterministic node (`nodes/phraseology_generator.py`) renders the structured decision into text, keeping "decide what to do" and "phrase it" as independently swappable stages (the seam a future fine-tuned phraseology model would slot into — see README §4).

---

## 2. LangGraph Multi-Role Architecture

```text
                          [observation_builder]
                                    |
                    (entry router: explicit active_role,
                     or anomaly-aircraft's classified role)
                                    |
        +--------------+--------------+--------------+
        v              v              v              v
  [clearance_      [ground_       [tower_        [approach_
   reasoning]       reasoning]     reasoning]      reasoning]
        |              |              |              |
        +------- reasoning_router (per node) ---------+
                    |         |         |
                    v         v         v
             [tool_executor] [handoff_handler] [phraseology_generator]
              (query_radar/    (HANDOFF: log +      |
               query_faa_       terminate or ->      v
               rules only)      phraseology;        END
                                 CONFLICT_CHECK:
                                 -> back to origin role)
```

`make_reasoning_node(role_name, llm, mcp_tools)` is the same factory called 4 times (was 2, for NORTH_TOWER/SOUTH_TOWER, in the original LAX design) — each closure binds the real MCP tools plus role-appropriate local tools (`finalize_instruction` and `advance_to_next_role` for all 4; `check_runway_conflict` for Tower only).

### Shared Graph State Schema

```python
from typing import TypedDict, Annotated, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class MultiRoleATCState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]

    active_role: str  # "CLEARANCE" | "GROUND" | "TOWER" | "APPROACH" | "IDLE"

    aircraft_by_role: dict[str, list[dict[str, Any]]]  # keyed by the 4 roles

    handoff_buffer: dict[str, Any]  # "kind": "HANDOFF" | "CONFLICT_CHECK" — see section 3

    active_anomalies: list[dict[str, Any]]

    final_instruction: dict[str, Any] | None
```

### Routing Logic

```python
def _reasoning_router(state: MultiRoleATCState) -> str:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", None) or []

    # advance_to_next_role / finalize_instruction / check_runway_conflict are handled
    # inline by the reasoning node itself — only a real MCP call reaches tool_executor.
    mcp_tool_calls = [tc for tc in tool_calls if tc["name"] not in _INLINE_HANDLED_TOOL_NAMES]
    if mcp_tool_calls:
        return "tool_executor"

    buffer = state.get("handoff_buffer") or {}
    if buffer.get("status") == "PENDING":
        return "handoff_handler"

    if state.get("final_instruction"):
        return "phraseology_generator"

    return "END"
```

---

## 3. Role Handoff Protocol

`nodes/handoff_handler.py` resolves exactly one of two request kinds, disambiguated by `handoff_buffer["kind"]`:

**`HANDOFF`** — a role handing an aircraft to an explicit `target_role` (via `advance_to_next_role`). No safety decision is made — it's always acknowledged. This is the *primary* interaction pattern in the 4-role chain (every aircraft moves through all 4 roles in sequence), unlike the original LAX design where cross-boundary coordination was a rare exception. Writes a durable audit row to `coordination_events` and does **not** re-invoke the target role's reasoning node within the same graph run — the next radio exchange happens via a separate trigger, mirroring how real ATC handoffs are separate radio calls, not one continuous exchange:

```json
{
  "handoff_buffer": {
    "kind": "HANDOFF",
    "status": "PENDING",
    "origin_role": "TOWER",
    "target_role": "APPROACH",
    "aircraft_id": "a1b2c3",
    "reason": "Departed Runway 7, passing 1,200 feet."
  }
}
```

**`CONFLICT_CHECK`** — Tower's runway-intersection safety check on itself (`check_runway_conflict`). Deliberately **not** an LLM judgment call — the project's "LLMs cannot do math" guardrail applies here as directly as it did to the original LAX design's cross-boundary distance check: whether an intersecting runway's approach is clear is a spatial question, answered by a real PostGIS distance query against the `runway` table's `intersects_with` data and threshold geometry, not model inference. Unlike `HANDOFF`, this **does** route back to the origin role's own reasoning node so it can act on the verdict:

```json
{
  "handoff_buffer": {
    "kind": "CONFLICT_CHECK",
    "status": "RESOLVED",
    "origin_role": "TOWER",
    "aircraft_id": "a1b2c3",
    "runway_id": "15R",
    "coordination_status": "COUNTER_PROPOSAL",
    "verification_note": "Traffic conflict: SKW9099 is 0.69 NM from Runway 25's threshold (< 2.0 NM minimum). Hold short."
  }
}
```

Both kinds persist a row to `coordination_events` — `handoff_buffer` itself is ephemeral LangGraph state that disappears when the graph run ends, so without this write, §4's coordination reward term would have nothing durable to read.

---

## 4. The Comprehensive Reward Function

To evaluate routing performance (offline replay scoring — see `evaluation/`, not live RL training), the reward function balances safety constraints as hard penalties against operational efficiency metrics. This formula and its component structure are unchanged from the original LAX design; what changed is what the **coordination** term measures.

The global reward $R_t$ at time step $t$ is a weighted linear combination of four components:

$$
R_t = w_s R_{\text{safety}} + w_t R_{\text{throughput}} + w_e R_{\text{efficiency}} + w_c R_{\text{coordination}}
$$

Where the weights are strictly bounded such that $w_s \gg w_t > w_e \ge w_c$ to prioritize safety above all else.

### 1. Safety Separation Penalty ($R_{\text{safety}}$)

Unchanged in structure from the original design — airport-agnostic. Let $d_{ij}$ be the lateral distance (NM) between aircraft $i$ and $j$, and $\Delta h_{ij}$ the vertical separation (ft):

$$
R_{\text{safety}} = \sum_{i \neq j} \left( P_{\text{lateral}}(d_{ij}) + P_{\text{vertical}}(\Delta h_{ij}) + P_{\text{wake}}(i, j) \right)
$$

$$
P_{\text{lateral}}(d_{ij}) = \begin{cases} 
0 & \text{if } d_{ij} \ge 3.0 \\
-\exp\left(\alpha \cdot (3.0 - d_{ij})\right) & \text{if } d_{ij} < 3.0 
\end{cases}
\qquad
P_{\text{vertical}}(\Delta h_{ij}) = \begin{cases} 
0 & \text{if } \Delta h_{ij} \ge 1000 \\
-\exp\left(\beta \cdot (1000 - \Delta h_{ij})\right) & \text{if } \Delta h_{ij} < 1000 
\end{cases}
$$

Implemented in `evaluation/reward.py::compute_safety_reward`, scored from `anomaly_events` (the exact separation recorded at the moment Phase 1's trigger fired) rather than a continuous per-timestep scan. $P_{\text{wake}}$ is currently an honest zero — no RECAT-category lookup exists yet (see the function's own docstring).

### 2. Throughput Reward ($R_{\text{throughput}}$)

Unchanged, airport-agnostic:

$$
R_{\text{throughput}} = \gamma_1 N_{\text{landed}} + \gamma_2 N_{\text{departed}} - \gamma_3 \sum_{i} T_{\text{delay}}(i)
$$

Implemented in `compute_throughput_reward` via `on_ground` transitions and standstill time in `aircraft_state_history`.

### 3. Fuel and Path Efficiency ($R_{\text{efficiency}}$)

Unchanged, airport-agnostic:

$$
R_{\text{efficiency}} = -\sum_{i} \left( c_1 \cdot \left| \psi_i^{(t)} - \psi_i^{(t-1)} \right| + c_2 \cdot \mathcal{D}_{\text{optimal}}(i) \right)
$$

Implemented in `compute_efficiency_reward` (heading-oscillation term only — $\mathcal{D}_{\text{optimal}}$ is an honest zero, no flight-plan destination data exists to compute it against).

### 4. Role-Handoff Coordination Penalty ($R_{\text{coordination}}$)

**This term's definition changed with the 4-role rescope.** The original LAX design measured uncoordinated *geographic crossings* between the North/South complexes. There is no such geography in the KSBA design — coordination now means every phase-of-flight transition should be accompanied by a recorded handoff:

$$
R_{\text{coordination}} = - \left( \lambda_1 N_{\text{unhandled\_transitions}} + \lambda_2 N_{\text{deadlocks}} \right)
$$

* $N_{\text{unhandled\_transitions}}$: an aircraft observed changing classified role (per `nodes/observation_builder.py`'s phase-of-flight classifier) within the scoring window, with no matching `coordination_events` `ROLE_HANDOFF` row nearby in time — the direct KSBA analogue of the old design's uncoordinated-crossing count.
* $N_{\text{deadlocks}}$: unchanged, an honest zero — no per-instruction ATC log exists to detect *why* an aircraft stalled, only that it did (already captured by $T_{\text{delay}}$ above).

**Known implementation gap:** `evaluation/reward.py::compute_coordination_reward` has not yet been updated to this definition — it still queries the retired `runway_complex` table from the original LAX design and will raise until reworked. This is a deliberate, documented deferral (see the function's own docstring and README §2's status table), not an oversight.
