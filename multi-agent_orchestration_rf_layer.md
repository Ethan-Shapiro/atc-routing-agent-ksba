
This specification defines the complete architectural blueprint and mathematical framework for the **Multi-Agent Orchestration and Reinforcement Learning** layer of your Autonomous ATC Routing Agent for LAX.

By splitting the airspace into localized **North Tower** and **South Tower** agents, we reduce the state-space dimensionality while introducing an explicit coordination channel for cross-complex operations.

---

## 1. Multi-Agent System (MAS) Formalization

The LAX terminal environment is modeled as a Markov Game where both agents operate simultaneously in a shared physical space but maintain specialized operational jurisdictions.

### Jurisdictional Domains

* **North Agent ($\mathcal{A}_N$):** Governs Runways 24L/24R, 6L/6R, Taxiways AA through GG, Terminals 1, 2, 3, and the north gates of Tom Bradley International Terminal (TBIT).
* **South Agent ($\mathcal{A}_S$):** Governs Runways 25L/25R, 7L/7R, Taxiways H through U, Terminals 4, 5, 6, 7, 8, and the south gates of TBIT.

### State, Observation, and Action Spaces

```text
+-----------------------------------------------------------------------+
|                         GLOBAL ENVIRONMENT (PostGIS)                  |
|  Aircraft Telemetry [X, Y, Z, V, psi] | Weather Cells | Runway States |
+-----------------------------------------------------------------------+
                     |                               |
        Obs Vector o_N |                               | Obs Vector o_S
                     v                               v
         +-----------------------+       +-----------------------+
         |   North Tower Agent   | <---> |   South Tower Agent   |
         |        (Active)       | Inter-|        (Active)       |
         +-----------------------+ Agent +-----------------------+
                     |             Comm              |
       Action a_N    v                               v Action a_S
+-----------------------------------------------------------------------+
|                      FLIGHT PATH MODIFICATIONS                        |
+-----------------------------------------------------------------------+
```

#### Observation Space ($o_t \subset \mathcal{S}$)

Each agent receives a localized observation vector $o_t$ filtered by its geospatial boundary via PostGIS, containing:

* **Aircraft Vectors:** For each aircraft $i$ within its sector: $vec_i = [x_i, y_i, z_i, v_i, \psi_i]$, representing lateral coordinates, altitude, groundspeed, and heading.
* **Wake Turbulence Category:** $w_i \in \{\text{Small}, \text{Large}, \text{Heavy}, \text{Super}\}$.
* **Spatial Constraints:** Active runway configurations, touchdown zones, and localized convective weather polygons.
* **Inbound Boundary Queue:** Telemetry of aircraft within 5 miles of crossing the North/South boundary line (Taxiline Tango / central alleyways).

#### Action Space ($a_t \subset \mathcal{A}$)

The output of each agent is a discrete-continuous hybrid action vector executed via the fine-tuned LLM:

$$
a_t = [ID_i, \text{CommandType}, \text{Value}]
$$

Where $\text{CommandType}$ maps to three standard deterministic interventions:

1. **Lateral Vectoring:** Change heading $\Delta \psi \in [-180^\circ, +180^\circ]$
2. **Vertical Separation:** Target altitude $z_{\text{target}}$ or vertical speed $\dot{z}$
3. **Speed Control:** Indicated airspeed adjustments $\Delta v$

---

## 2. LangGraph Multi-Agent Architecture

The orchestration layer uses an extended multi-agent ReAct loop. The graph manages two independent reasoning loops that sync state data through a shared scratchpad when cross-boundary anomalies occur.

```text
       +----------------------- START -----------------------+
       |                                                     |
       v                                                     v
[North Reasoning]                                     [South Reasoning]
       |                                                     |
       +-----------------> [Conditional Router] <------------+
                                    |
                    +---------------+---------------+
                    |                               |
                    v                               v
            [Tool Executor]               [Inter-Agent Comm Buffer]
             (PostGIS / VectorDB)          (Coordinate Hand-offs)
```

### Shared Graph State Schema

```python
from typing import TypedDict, Annotated, List, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class MultiAgentATCState(TypedDict):
    # Core message history for LLM tracking
    messages: Annotated[List[BaseMessage], add_messages]
  
    # Track which agent currently holds execution focus
    active_agent: str  # "NORTH_TOWER" | "SOUTH_TOWER" | "COORDINATOR"
  
    # Active operational states for both complexes
    north_aircraft: List[Dict[str, Any]]
    south_aircraft: List[Dict[str, Any]]
  
    # The message frame used to pass constraints between agents
    inter_agent_buffer: Dict[str, Any]
  
    # Global tracking of active systemic anomalies
    active_anomalies: List[Dict[str, Any]]
```

### Routing Logic

The conditional router evaluates if an agent's planned action impacts the opposite complex's geography (e.g., a North arrival missing an approach and climbing into the South departure path).

```python
def multi_agent_router(state: MultiAgentATCState) -> str:
    """Evaluates the last action to determine the next graph node."""
    last_message = state["messages"][-1]
  
    # Check if a tool call was requested
    if hasattr(last_message, "tool_calls") and len(last_message.tool_calls) > 0:
        return "tool_executor"
  
    # Check if cross-boundary communication is required
    buffer = state.get("inter_agent_buffer", {})
    if buffer.get("requires_coordination") and state["active_agent"] != "COORDINATOR":
        return "inter_agent_comm_handler"
      
    return "END"
```

---

## 3. Inter-Agent Communication Protocol

To prevent the agents from generating conflicting instructions, they pass structured coordination tokens through the `inter_agent_buffer`.

When the North Agent detects an event that forces an aircraft to cross south of Taxiline Tango, it updates the state with a coordination contract before generating phraseology:

```json
{
  "inter_agent_buffer": {
    "requires_coordination": true,
    "origin_agent": "NORTH_TOWER",
    "target_agent": "SOUTH_TOWER",
    "aircraft_id": "AAL423",
    "proposed_action": {
      "type": "TAXI_CROSSING",
      "coordinate_threshold": [33.944, -118.408],
      "estimated_time_crossing": "16:54:10Z"
    },
    "coordination_status": "PENDING"
  }
}
```

The South Agent must ingest this buffer token during its next execution step, verify spacing on its side using `query_radar`, and either return an `ACK` or update the state with a `COUNTER_PROPOSAL` constraint.

---

## 4. The Comprehensive Reward Function

To train or evaluate your routing agent effectively, you need a mathematically rigorous reward function. The objective function balances safety constraints as hard penalties against operational efficiency metrics.

The global reward $R_t$ at time step $t$ is calculated as a weighted linear combination of four components:

$$
R_t = w_s R_{\text{safety}} + w_t R_{\text{throughput}} + w_e R_{\text{efficiency}} + w_c R_{\text{coordination}}
$$

Where the weights are strictly bounded such that $w_s \gg w_t > w_e \ge w_c$ to prioritize safety above all else.

### 1. Safety Separation Penalty ($R_{\text{safety}}$)

This handles the strict minimum separation rules defined in FAA Order JO 7110.65. Let $d_{ij}$ be the lateral distance (nautical miles) between aircraft $i$ and $j$, and $\Delta h_{ij}$ be the vertical separation (feet).

$$
R_{\text{safety}} = \sum_{i \neq j} \left( P_{\text{lateral}}(d_{ij}) + P_{\text{vertical}}(\Delta h_{ij}) + P_{\text{wake}}(i, j) \right)
$$

The lateral and vertical penalties are modeled using a continuous exponential decay function to punish the agent heavily as aircraft approach the separation threshold:

$$
P_{\text{lateral}}(d_{ij}) = \begin{cases} 
0 & \text{if } d_{ij} \ge 3.0 \\
-\exp\left(\alpha \cdot (3.0 - d_{ij})\right) & \text{if } d_{ij} < 3.0 
\end{cases}
$$

$$
P_{\text{vertical}}(\Delta h_{ij}) = \begin{cases} 
0 & \text{if } \Delta h_{ij} \ge 1000 \\
-\exp\left(\beta \cdot (1000 - \Delta h_{ij})\right) & \text{if } \Delta h_{ij} < 1000 
\end{cases}
$$

Where $\alpha$ and $\beta$ are scaling hyperparameters designed to scale the penalty rapidly toward negative infinity if distances breach critical thresholds ($d_{ij} < 1.0 \text{ NM}$).

The wake turbulence penalty enforces stricter distances based on weight category pairings (e.g., a Small aircraft following a Super):

$$
P_{\text{wake}}(i, j) = \begin{cases} 
-1000 & \text{if } d_{ij} < D_{\text{wake}}(w_i, w_j) \\
0 & \text{otherwise}
\end{cases}
$$

### 2. Throughput Reward ($R_{\text{throughput}}$)

Encourages the system to maintain stable traffic flows without stalling aircraft or creating infinite holding patterns.

$$
R_{\text{throughput}} = \gamma_1 N_{\text{landed}} + \gamma_2 N_{\text{departed}} - \gamma_3 \sum_{i} T_{\text{delay}}(i)
$$

* $N_{\text{landed}}, N_{\text{departed}}$: Count of successfully managed handoffs out of the terminal environment during time step $t$.
* $T_{\text{delay}}(i)$: Total time aircraft $i$ spends at a complete standstill on taxiways or in holding patterns beyond its nominal flight time.

### 3. Fuel and Path Efficiency ($R_{\text{efficiency}}$)

Penalizes excessive vectoring or erratic path adjustments.

$$
R_{\text{efficiency}} = -\sum_{i} \left( c_1 \cdot \left| \psi_i^{(t)} - \psi_i^{(t-1)} \right| + c_2 \cdot \mathcal{D}_{\text{optimal}}(i) \right)
$$

* $\left| \psi_i^{(t)} - \psi_i^{(t-1)} \right|$: Penalizes high-frequency heading oscillations (unstable commands).
* $\mathcal{D}_{\text{optimal}}(i)$: The actual flight path distance traveled minus the geodesic path distance to the destination fix.

### 4. Cross-Complex Coordination Penalty ($R_{\text{coordination}}$)

Penalizes uncoordinated interactions between the North and South complexes.

$$
R_{\text{coordination}} = - \left( \lambda_1 N_{\text{uncoordinated\_crossings}} + \lambda_2 N_{\text{deadlocks}} \right)
$$

* $N_{\text{uncoordinated\_crossings}}$: Increments if an agent issues a routing vector across the central boundary line without an approved (`ACK`) state contract in the `inter_agent_buffer`.
* $N_{\text{deadlocks}}$: Increments if the North and South agents issue conflicting taxi or arrival instructions to the same aircraft, resulting in zero forward velocity.
