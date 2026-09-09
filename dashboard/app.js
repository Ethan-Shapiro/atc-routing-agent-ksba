// KSBA ATC dashboard — no build step, plain fetch() against agent_orchestration's API
// (same-origin, served from the same FastAPI app via the /dashboard static mount).

const SVG_NS = "http://www.w3.org/2000/svg";
const VIEWBOX_W = 600;
const VIEWBOX_H = 400;
const MARGIN = 60;

// Each scenario "leg" (the flight between one step's position and the next) animates over
// this many milliseconds, independent of how long the real /trigger call actually takes —
// a 4-step departure/arrival plays out over roughly this * 4 seconds of continuous motion,
// which is the whole point: the real LLM latency is unpredictable, the flight isn't.
const LEG_DURATION_MS = 7000;

let projection = null; // {lon0, lat0, scale, cx, cy} — set once /airport/layout loads
let aircraftGroup = null; // <g> wrapping the icon, translated+rotated as a unit
let flightTrail = null; // <polyline> tracing the path flown so far this scenario
let trailPoints = [];
let currentSvgPos = null; // {x, y} in SVG space — the marker's actual current position

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
  for (const r of layout.runways) {
    for (const [lon, lat] of [
      [r.threshold_lon, r.threshold_lat],
      [r.centerline_start_lon, r.centerline_start_lat],
      [r.centerline_end_lon, r.centerline_end_lat],
    ]) {
      const { dx, dy } = metersFromLonLat(lon, lat, lon0, lat0);
      maxAbsX = Math.max(maxAbsX, Math.abs(dx));
      maxAbsY = Math.max(maxAbsY, Math.abs(dy));
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

  flightTrail = svgEl("polyline", { class: "flight-trail", points: "" });
  svg.appendChild(flightTrail);

  // A small dart/plane shape pointing "up" (bearing 0) in its own local coordinate space —
  // rotated + translated as a unit via the wrapping <g>'s transform, never redrawn per frame.
  aircraftGroup = svgEl("g", { class: "aircraft-marker", style: "opacity: 0" });
  aircraftGroup.appendChild(svgEl("path", { d: "M 0,-9 L 5,7 L 0,4 L -5,7 Z", class: "aircraft-icon" }));
  svg.appendChild(aircraftGroup);

  for (const roleInfo of layout.roles) {
    const box = document.querySelector(`.controller-box[data-role="${roleInfo.role}"]`);
    if (box) box.querySelector(".frequency").textContent = `${roleInfo.frequency_mhz} MHz`;
  }
}

function headingDeg(fromPos, toPos) {
  const dx = toPos.x - fromPos.x;
  const dy = toPos.y - fromPos.y;
  return (Math.atan2(dx, -dy) * 180) / Math.PI; // 0 = up, clockwise — matches aviation headings
}

function setMarkerTransform(pos, angle, onGround) {
  aircraftGroup.setAttribute("transform", `translate(${pos.x}, ${pos.y}) rotate(${angle})`);
  aircraftGroup.classList.toggle("on-ground", !!onGround);
}

function resetMarker(lonLat) {
  trailPoints = [];
  if (!aircraftGroup || !projection) return;
  if (!lonLat) {
    aircraftGroup.style.opacity = "0";
    currentSvgPos = null;
    return;
  }
  currentSvgPos = project(lonLat.longitude, lonLat.latitude);
  aircraftGroup.style.opacity = "1";
  setMarkerTransform(currentSvgPos, 0, false);
  trailPoints.push(`${currentSvgPos.x},${currentSvgPos.y}`);
  flightTrail.setAttribute("points", trailPoints.join(" "));
}

// Eases the marker from its current position to lonLat over durationMs. Fire-and-forget —
// see runStep's comment on why nothing awaits this promise for correctness. Safe to call with
// null (nothing to animate toward, e.g. a Clearance Delivery step where the aircraft has no
// radar position).
function animateMarkerTo(lonLat, durationMs, onGround) {
  if (!aircraftGroup || !projection || !lonLat) return Promise.resolve();

  const target = project(lonLat.longitude, lonLat.latitude);
  const start = currentSvgPos || target;
  const angle = headingDeg(start, target);
  aircraftGroup.style.opacity = "1";

  function snapToTarget() {
    currentSvgPos = target;
    trailPoints.push(`${target.x},${target.y}`);
    flightTrail.setAttribute("points", trailPoints.join(" "));
    setMarkerTransform(target, angle, onGround);
  }

  // Nothing to animate toward if already there, or if the tab is hidden — requestAnimationFrame
  // doesn't fire on a hidden tab, so there's no point starting a loop that won't render; snap
  // straight to the end position instead.
  if ((start.x === target.x && start.y === target.y) || document.hidden) {
    snapToTarget();
    return Promise.resolve();
  }

  return new Promise((resolve) => {
    const startTime = performance.now();
    function tick(now) {
      const t = Math.min((now - startTime) / durationMs, 1);
      const eased = t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; // ease-in-out
      const pos = { x: start.x + (target.x - start.x) * eased, y: start.y + (target.y - start.y) * eased };
      setMarkerTransform(pos, angle, onGround);
      if (t < 1) {
        requestAnimationFrame(tick);
      } else {
        snapToTarget();
        resolve();
      }
    }
    requestAnimationFrame(tick);
  });
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

  // Kicked off in parallel with the real API call, not after it — the plane is "flying"
  // while the radio exchange is in progress, same as real ATC. Deliberately NOT awaited
  // alongside the fetch, though: it resolves via requestAnimationFrame, which a hidden or
  // merely-occluded browser tab can throttle indefinitely with no event this page can observe
  // (visibilitychange only fires for actual tab-switches, not window occlusion) — gating
  // scenario progression on it risks freezing the whole run with no error and no way to
  // recover short of a reload. A plane that's a few seconds behind its radio call is a much
  // smaller problem than that, so only the fetch decides when to move on.
  if (step.position) {
    animateMarkerTo(step.position, LEG_DURATION_MS, step.position.on_ground);
  }

  const res = await fetch(`/trigger/${step.role.toLowerCase()}/${step.icao24}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ context: step.context, position: step.position || null }),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${step.role} step failed (${res.status}): ${text}`);
  }
  const data = await res.json();

  // Only relevant if the step had no scripted position (e.g. Clearance) but the aircraft
  // does have a real radar position by now — snap to it rather than leaving the marker
  // wherever the last animated leg left it.
  if (!step.position && data.aircraft_position) {
    resetMarker(data.aircraft_position);
  }

  setControllerMessage(step.role, data.final_message || "(no instruction issued)");
  appendTranscript({
    kind: "response",
    header: `${step.role} responds`,
    message: data.final_message || "(no instruction issued — no action needed)",
    toolCalls: data.tool_calls,
  });
}

async function playScenario(scenario, buttonsContainer) {
  buttonsContainer.querySelectorAll("button").forEach((b) => (b.disabled = true));
  document.getElementById("transcript").innerHTML = "";

  try {
    appendTranscript({ kind: "system", header: "Seeding scenario…", message: scenario.id });
    const resetRes = await fetch(`/scenarios/${scenario.id}/reset`, { method: "POST" });
    if (!resetRes.ok) throw new Error(`Failed to seed scenario (${resetRes.status})`);

    const firstIcao24 = scenario.steps[0]?.icao24;
    resetMarker(firstIcao24 ? scenario.seed_positions[firstIcao24] : null);

    for (const step of scenario.steps) {
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
    button.addEventListener("click", () => playScenario(scenario, container));
    card.appendChild(button);

    container.appendChild(card);
  }
}

// ---------------------------------------------------------------------------------------
// Recorded Day replay: real recorded tracks looped as smooth motion, agents firing on real
// events (server-side, cached). The plane positions are interpolated locally against a clock
// synced to the server's replay clock — so motion stays smooth between the ~1s status polls.
// ---------------------------------------------------------------------------------------

const replay = {
  sessionId: null,
  tracks: [],
  startedMs: null,
  endedMs: null,
  serverClockMs: null,
  lastSyncWall: null,
  speed: 60,
  playing: false,
  layer: null,
  markers: new Map(),
  rafId: null,
  statusTimer: null,
  txTimer: null,
  seenTx: new Set(),
  lastLocalMs: null,
};

async function loadReplaySessions() {
  const res = await fetch("/replay/sessions");
  const data = await res.json();
  const select = document.getElementById("replay-session");
  select.innerHTML = "";
  if (!data.sessions.length) {
    select.innerHTML = `<option value="">No recordings yet — click Record</option>`;
    return;
  }
  for (const s of data.sessions) {
    const opt = document.createElement("option");
    opt.value = s.id;
    const mins = Math.round((Date.parse(s.ended_at) - Date.parse(s.started_at)) / 60000);
    opt.textContent = `[${s.id}] ${s.label} — ${mins} min, ${s.ifr_aircraft} aircraft`;
    select.appendChild(opt);
  }
}

function replayInterpolate(track, clockMs) {
  const pts = track.points;
  if (!pts.length || clockMs < pts[0].tMs || clockMs > pts[pts.length - 1].tMs) return null;
  let lo = 0;
  let hi = pts.length - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (pts[mid].tMs <= clockMs) lo = mid;
    else hi = mid;
  }
  const a = pts[lo];
  const b = pts[hi];
  const span = b.tMs - a.tMs || 1;
  const f = Math.min(Math.max((clockMs - a.tMs) / span, 0), 1);
  return {
    lon: a.lon + (b.lon - a.lon) * f,
    lat: a.lat + (b.lat - a.lat) * f,
    on_ground: f < 0.5 ? a.on_ground : b.on_ground,
    prev: a,
    next: b,
  };
}

function replayMarker(icao24, callsign) {
  let m = replay.markers.get(icao24);
  if (m) return m;
  const group = svgEl("g", { class: "aircraft-marker replay-plane", style: "opacity: 0" });
  group.appendChild(svgEl("path", { d: "M 0,-9 L 5,7 L 0,4 L -5,7 Z", class: "aircraft-icon" }));
  const label = svgEl("text", { class: "replay-plane-label", x: 8, y: 4 });
  label.textContent = callsign || icao24;
  group.appendChild(label);
  replay.layer.appendChild(group);
  m = { group, label };
  replay.markers.set(icao24, m);
  return m;
}

function replayLocalClockMs() {
  if (replay.serverClockMs == null) return null;
  const c = replay.serverClockMs + (performance.now() - replay.lastSyncWall) * replay.speed;
  return Math.min(c, replay.endedMs);
}

function replayFrame() {
  const clock = replayLocalClockMs();
  if (clock != null) {
    const clockEl = document.getElementById("replay-clock");
    clockEl.textContent = new Date(clock).toISOString().substr(11, 8) + " UTC";
    for (const track of replay.tracks) {
      const pos = replayInterpolate(track, clock);
      const m = replay.markers.get(track.icao24);
      if (!pos) {
        if (m) m.group.style.opacity = "0";
        continue;
      }
      const marker = m || replayMarker(track.icao24, track.callsign);
      const p = project(pos.lon, pos.lat);
      const from = project(pos.prev.lon, pos.prev.lat);
      const to = project(pos.next.lon, pos.next.lat);
      const angle = from.x === to.x && from.y === to.y ? 0 : headingDeg(from, to);
      marker.group.setAttribute("transform", `translate(${p.x}, ${p.y}) rotate(${angle})`);
      marker.group.classList.toggle("on-ground", !!pos.on_ground);
      marker.group.style.opacity = "1";
    }
  }
  replay.rafId = requestAnimationFrame(replayFrame);
}

async function replayPollStatus() {
  try {
    const res = await fetch("/replay/status");
    const s = await res.json();
    if (!s.playing) {
      if (replay.playing) stopReplayUI();
      return;
    }
    const newClockMs = Date.parse(s.clock);
    // A big backward jump = the loop restarted: re-announce transmissions and reset boxes.
    if (replay.lastLocalMs != null && newClockMs < replay.lastLocalMs - 5000) {
      replay.seenTx.clear();
      document.querySelectorAll(".controller-box .latest-message").forEach((e) => (e.textContent = "Idle"));
    }
    replay.serverClockMs = newClockMs;
    replay.lastSyncWall = performance.now();
    replay.speed = s.speed;
    replay.lastLocalMs = newClockMs;
  } catch (err) {
    console.error("replay status poll failed", err);
  }
}

async function replayPollTransmissions() {
  if (replay.sessionId == null) return;
  try {
    const res = await fetch(`/replay/${replay.sessionId}/transmissions`);
    const data = await res.json();
    const clock = replayLocalClockMs();
    for (const tx of data.transmissions) {
      const txMs = Date.parse(tx.replay_time);
      const key = `${tx.icao24}:${tx.role}:${tx.replay_time}`;
      if (txMs > clock || replay.seenTx.has(key)) continue;
      replay.seenTx.add(key);
      setActiveRole(tx.role);
      setControllerMessage(tx.role, tx.text || "(no instruction)");
      appendTranscript({
        kind: "response",
        header: `${tx.role} → ${tx.callsign || tx.icao24}  ·  ${new Date(txMs).toISOString().substr(11, 8)}Z`,
        message: tx.text || "(no instruction issued)",
        toolCalls: tx.tool_calls,
      });
    }
  } catch (err) {
    console.error("replay transmissions poll failed", err);
  }
}

async function startReplay() {
  const sessionId = document.getElementById("replay-session").value;
  if (!sessionId) return;
  const speed = Number(document.getElementById("replay-speed").value);
  const loop = document.getElementById("replay-loop").checked;

  // Take over the map from any scenario run.
  if (aircraftGroup) aircraftGroup.style.opacity = "0";
  if (flightTrail) flightTrail.setAttribute("points", "");
  document.getElementById("transcript").innerHTML = "";
  document.querySelectorAll("#scenario-list button").forEach((b) => (b.disabled = true));

  const tracksRes = await fetch(`/replay/${sessionId}/tracks`);
  const tracksData = await tracksRes.json();
  replay.sessionId = Number(sessionId);
  replay.startedMs = Date.parse(tracksData.started_at);
  replay.endedMs = Date.parse(tracksData.ended_at);
  replay.tracks = tracksData.tracks.map((t) => ({
    ...t,
    points: t.points.map((p) => ({ ...p, tMs: Date.parse(p.t) })),
  }));
  replay.seenTx.clear();
  replay.lastLocalMs = null;

  if (!replay.layer) {
    replay.layer = svgEl("g", { id: "replay-layer" });
    document.getElementById("airport-map").appendChild(replay.layer);
  }
  replay.layer.innerHTML = "";
  replay.markers.clear();

  const startRes = await fetch(`/replay/${sessionId}/start`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ speed, loop }),
  });
  if (!startRes.ok) {
    appendTranscript({ kind: "error", header: "Replay failed to start", message: await startRes.text() });
    document.querySelectorAll("#scenario-list button").forEach((b) => (b.disabled = false));
    return;
  }
  const status = await startRes.json();
  replay.playing = true;
  replay.speed = speed;
  replay.serverClockMs = Date.parse(status.clock);
  replay.lastSyncWall = performance.now();

  document.getElementById("replay-play").disabled = true;
  document.getElementById("replay-stop").disabled = false;
  appendTranscript({ kind: "system", header: "Replay started", message: `${status.label} @ ${speed}× (agents fire on real events; loop 2+ is cached)` });

  replay.statusTimer = setInterval(replayPollStatus, 1000);
  replay.txTimer = setInterval(replayPollTransmissions, 1500);
  replay.rafId = requestAnimationFrame(replayFrame);
}

function stopReplayUI() {
  replay.playing = false;
  if (replay.rafId) cancelAnimationFrame(replay.rafId);
  if (replay.statusTimer) clearInterval(replay.statusTimer);
  if (replay.txTimer) clearInterval(replay.txTimer);
  replay.rafId = replay.statusTimer = replay.txTimer = null;
  if (replay.layer) replay.layer.innerHTML = "";
  replay.markers.clear();
  document.getElementById("replay-play").disabled = false;
  document.getElementById("replay-stop").disabled = true;
  document.querySelectorAll("#scenario-list button").forEach((b) => (b.disabled = false));
  setActiveRole(null);
}

async function stopReplay() {
  await fetch("/replay/stop", { method: "POST" }).catch(() => {});
  stopReplayUI();
  appendTranscript({ kind: "system", header: "Replay stopped", message: "" });
}

async function replayInit() {
  await loadReplaySessions();
  document.getElementById("replay-play").addEventListener("click", startReplay);
  document.getElementById("replay-stop").addEventListener("click", stopReplay);
  document.getElementById("replay-record").addEventListener("click", async () => {
    const label = prompt("Name this recording:", "KSBA " + new Date().toISOString().substr(11, 5));
    if (!label) return;
    await fetch("/replay/sessions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label, last_minutes: 30 }),
    });
    await loadReplaySessions();
  });
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

  try {
    await replayInit();
  } catch (err) {
    console.error("Failed to init replay:", err);
    document.getElementById("replay-controls").innerHTML = `<p class="load-error">Failed to load replay: ${err}</p>`;
  }
})();
