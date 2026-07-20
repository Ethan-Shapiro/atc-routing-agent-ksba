// KSBA ATC dashboard — no build step, plain fetch() against agent_orchestration's API
// (same-origin, served from the same FastAPI app via the /dashboard static mount).

const SVG_NS = "http://www.w3.org/2000/svg";
const VIEWBOX_W = 600;
const VIEWBOX_H = 400;
const MARGIN = 60;

let projection = null; // {lon0, lat0, scale, cx, cy} — set once /airport/layout loads
let aircraftMarker = null;

function metersFromLonLat(lon, lat, lon0, lat0) {
  const dx = (lon - lon0) * Math.cos((lat0 * Math.PI) / 180) * 111320;
  const dy = (lat - lat0) * 111320;
  return { dx, dy };
}

function project(lon, lat) {
  const { dx, dy } = metersFromLonLat(lon, lat, projection.lon0, projection.lat0);
  // SVG y grows downward; north (positive dy) should go up, so subtract.
  return { x: projection.cx + dx * projection.scale, y: projection.cy - dy * projection.scale };
}

function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  return el;
}

async function loadAirportLayout() {
  const res = await fetch("/airport/layout");
  const layout = await res.json();

  // Fit projection scale to the bounding box of every runway point, with margin.
  const lon0 = layout.airport_lon;
  const lat0 = layout.airport_lat;
  let maxAbsX = 1;
  let maxAbsY = 1;
  const points = [];
  for (const r of layout.runways) {
    for (const [lon, lat] of [
      [r.threshold_lon, r.threshold_lat],
      [r.centerline_start_lon, r.centerline_start_lat],
      [r.centerline_end_lon, r.centerline_end_lat],
    ]) {
      const { dx, dy } = metersFromLonLat(lon, lat, lon0, lat0);
      maxAbsX = Math.max(maxAbsX, Math.abs(dx));
      maxAbsY = Math.max(maxAbsY, Math.abs(dy));
      points.push([lon, lat]);
    }
  }
  const scaleX = (VIEWBOX_W / 2 - MARGIN) / maxAbsX;
  const scaleY = (VIEWBOX_H / 2 - MARGIN) / maxAbsY;
  projection = {
    lon0,
    lat0,
    scale: Math.min(scaleX, scaleY),
    cx: VIEWBOX_W / 2,
    cy: VIEWBOX_H / 2,
  };

  const svg = document.getElementById("airport-map");
  svg.innerHTML = "";

  // Each physical strip appears twice (once per threshold end) — draw each centerline once.
  const drawn = new Set();
  for (const r of layout.runways) {
    const key = [r.centerline_start_lon, r.centerline_start_lat, r.centerline_end_lon, r.centerline_end_lat]
      .map((n) => n.toFixed(5))
      .join(",");
    if (drawn.has(key)) continue;
    drawn.add(key);

    const start = project(r.centerline_start_lon, r.centerline_start_lat);
    const end = project(r.centerline_end_lon, r.centerline_end_lat);
    svg.appendChild(
      svgEl("line", { x1: start.x, y1: start.y, x2: end.x, y2: end.y, class: "runway-strip" })
    );
  }

  for (const r of layout.runways) {
    const p = project(r.threshold_lon, r.threshold_lat);
    svg.appendChild(svgEl("circle", { cx: p.x, cy: p.y, r: 3, class: "runway-threshold" }));
    const label = svgEl("text", { x: p.x + 6, y: p.y - 6, class: "runway-label" });
    label.textContent = r.runway_id;
    svg.appendChild(label);
  }

  aircraftMarker = svgEl("circle", { cx: -100, cy: -100, r: 6, class: "aircraft-marker" });
  svg.appendChild(aircraftMarker);

  for (const roleInfo of layout.roles) {
    const box = document.querySelector(`.controller-box[data-role="${roleInfo.role}"]`);
    if (box) box.querySelector(".frequency").textContent = `${roleInfo.frequency_mhz} MHz`;
  }
}

function updateAircraftMarker(position) {
  if (!aircraftMarker) return; // map failed to load — transcript/controller strip still work
  if (!position || !projection) {
    aircraftMarker.setAttribute("cx", -100);
    aircraftMarker.setAttribute("cy", -100);
    return;
  }
  const p = project(position.longitude, position.latitude);
  aircraftMarker.setAttribute("cx", p.x);
  aircraftMarker.setAttribute("cy", p.y);
}

function appendTranscript(entry) {
  const div = document.createElement("div");
  div.className = `transcript-entry ${entry.kind}`;
  const header = document.createElement("div");
  header.className = "transcript-header";
  header.textContent = entry.header;
  div.appendChild(header);

  if (entry.toolCalls && entry.toolCalls.length) {
    for (const call of entry.toolCalls) {
      const line = document.createElement("div");
      line.className = "tool-call-line";
      line.textContent = `🔧 ${call.name}(${JSON.stringify(call.args)})`;
      div.appendChild(line);
    }
  }

  const message = document.createElement("div");
  message.className = "transcript-message";
  message.textContent = entry.message;
  div.appendChild(message);

  const transcript = document.getElementById("transcript");
  transcript.appendChild(div);
  transcript.scrollTop = transcript.scrollHeight;
}

function setActiveRole(role) {
  document.querySelectorAll(".controller-box").forEach((box) => {
    box.classList.toggle("active", box.dataset.role === role);
  });
}

function setControllerMessage(role, message) {
  const box = document.querySelector(`.controller-box[data-role="${role}"]`);
  if (box) box.querySelector(".latest-message").textContent = message;
}

async function runStep(step) {
  setActiveRole(step.role);
  appendTranscript({ kind: "pending", header: `${step.role} — ${step.label}…`, message: step.context || "" });

  const res = await fetch(`/trigger/${step.role.toLowerCase()}/${step.icao24}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context: step.context }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${step.role} step failed (${res.status}): ${text}`);
  }
  const data = await res.json();

  setControllerMessage(step.role, data.final_message || "(no instruction issued)");
  updateAircraftMarker(data.aircraft_position);
  appendTranscript({
    kind: "response",
    header: `${step.role} responds`,
    message: data.final_message || "(no instruction issued — no action needed)",
    toolCalls: data.tool_calls,
  });
}

async function playScenario(scenarioId, steps, buttonsContainer) {
  buttonsContainer.querySelectorAll("button").forEach((b) => (b.disabled = true));
  document.getElementById("transcript").innerHTML = "";
  updateAircraftMarker(null);

  try {
    appendTranscript({ kind: "system", header: "Seeding scenario…", message: scenarioId });
    const resetRes = await fetch(`/scenarios/${scenarioId}/reset`, { method: "POST" });
    if (!resetRes.ok) throw new Error(`Failed to seed scenario (${resetRes.status})`);

    for (const step of steps) {
      await runStep(step);
    }
    appendTranscript({ kind: "system", header: "Scenario complete", message: "" });
  } catch (err) {
    appendTranscript({ kind: "error", header: "Error", message: String(err) });
  } finally {
    setActiveRole(null);
    buttonsContainer.querySelectorAll("button").forEach((b) => (b.disabled = false));
  }
}

async function loadScenarios() {
  const res = await fetch("/scenarios");
  const data = await res.json();
  const container = document.getElementById("scenario-list");
  container.innerHTML = "";

  for (const scenario of data.scenarios) {
    const card = document.createElement("div");
    card.className = "scenario-card";

    const title = document.createElement("div");
    title.className = "scenario-title";
    title.textContent = scenario.title;
    card.appendChild(title);

    const desc = document.createElement("div");
    desc.className = "scenario-description";
    desc.textContent = scenario.description;
    card.appendChild(desc);

    const button = document.createElement("button");
    button.textContent = "Run";
    button.addEventListener("click", () => playScenario(scenario.id, scenario.steps, container));
    card.appendChild(button);

    container.appendChild(card);
  }
}

(async function init() {
  // Independent try/catches: a map-loading failure (e.g. a transient DB hiccup) should
  // never block the scenario list from loading, and vice versa — the two are unrelated
  // features that both happen to fetch data on page load.
  try {
    await loadAirportLayout();
  } catch (err) {
    console.error("Failed to load airport layout:", err);
    document.getElementById("map-section").innerHTML = `<p class="load-error">Failed to load airport diagram: ${err}</p>`;
  }

  try {
    await loadScenarios();
  } catch (err) {
    console.error("Failed to load scenarios:", err);
    document.getElementById("scenario-list").innerHTML = `<p class="load-error">Failed to load scenarios: ${err}</p>`;
  }
})();
