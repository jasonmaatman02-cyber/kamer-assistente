// Overview page.
// One /api/overview call per cycle (system + services + weather + agenda +
// now-playing + notes) instead of 5 separate requests — matters on slow wifi.
// Now-playing gets an extra lightweight poll so it stays snappy.

function jget(url, ms = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { signal: ctrl.signal })
    .then(r => r.json())
    .finally(() => clearTimeout(t));
}
function jpost(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  }).then(r => r.json());
}
const $ = id => document.getElementById(id);
const msToTime = ms => {
  const s = Math.floor((ms || 0) / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};

// ---------- renderers (all take pre-fetched data) ----------
function bar(pct) {
  const cls = pct >= 90 ? "crit" : pct >= 75 ? "warn" : "";
  return `<div class="bar"><span class="${cls}" style="width:${Math.min(100, pct)}%"></span></div>`;
}
function renderSystem(d) {
  if (!d || d.error) { $("sys-stats").innerHTML = `<p class="empty">${d?.error || "niet beschikbaar"}</p>`; return; }
  const rows = [
    ["CPU", d.cpu_percent, `${d.cpu_percent}%`],
    ["Memory", d.mem_percent, `${d.mem_used_mb}/${d.mem_total_mb} MB`],
    ["Disk", d.disk_percent, `${d.disk_free_gb} GB vrij`],
  ];
  let html = rows.map(([l, p, t]) => `<div class="stat-row"><span>${l}</span>${bar(p)}<span>${t}</span></div>`).join("");
  if (d.cpu_temp != null) html += `<div class="stat-row"><span>Temp</span><span>${d.cpu_temp} °C</span></div>`;
  if (d.uptime_s != null) {
    const h = Math.floor(d.uptime_s / 3600), m = Math.floor((d.uptime_s % 3600) / 60);
    html += `<div class="stat-row"><span>Uptime</span><span>${h}u ${m}m</span></div>`;
  }
  $("sys-stats").innerHTML = html;
}
function renderServices(d) {
  if (!d) return;
  const label = { spotify: "Spotify", radio: "Radio", weer: "Weer", agenda: "Agenda" };
  $("service-status").innerHTML = Object.entries(d).map(([k, v]) =>
    `<p><i class="fa fa-circle ${v.ok ? "green" : "red"}"></i> ${label[k] || k}${v.error ? ` <span class="muted">(${v.error})</span>` : ""}</p>`
  ).join("");
  const conn = $("conn-status");
  const down = Object.values(d).filter(v => !v.ok).length;
  if (conn) {
    conn.className = "status" + (down === 0 ? "" : down >= 3 ? " offline" : " degraded");
    conn.innerHTML = `<i class="fa fa-circle"></i> ${down === 0 ? "Connected" : down >= 3 ? "Offline" : "Degraded"}`;
  }
}
function renderWeather(d) {
  if (!d || d.error) { $("w-title").textContent = "Weer niet beschikbaar"; return; }
  $("w-title").textContent = d.city;
  $("w-title").classList.remove("muted");
  $("w-temp").textContent = `${d.temp} °C`;
  $("w-condition").textContent = d.condition;
  $("w-humidity").textContent = `Vochtigheid: ${d.humidity}%`;
  $("w-wind").textContent = `Wind: ${d.wind} km/h`;
}
function renderAgenda(d) {
  const box = document.querySelector(".agenda-list");
  box.innerHTML = (d && d.events && d.events.length)
    ? d.events.map(ev => `<p>${ev}</p>`).join("")
    : `<p class="empty">${d && d.error ? "Agenda: " + d.error : "Geen events vandaag."}</p>`;
}
function renderNotes(notes) {
  const box = $("notes-list");
  box.innerHTML = (notes && notes.length)
    ? notes.slice(-6).reverse().map(n => `<p>• ${n}</p>`).join("")
    : '<p class="empty">Nog geen notities.</p>';
}
let npState = { type: "none" };
function renderNowPlaying(d) {
  npState = d || { type: "none" };
  if (d && d.type === "spotify") {
    $("np-thumb").src = d.thumbnail || "/static/images/radio.png";
    $("np-title").textContent = d.name;
    $("np-artist").textContent = `${d.artist} • ${d.album}`;
    $("np-progress").max = d.duration_ms; $("np-progress").value = d.progress_ms;
    $("np-current-time").textContent = msToTime(d.progress_ms);
    $("np-duration").textContent = msToTime(d.duration_ms);
    $("np-play-pause").innerHTML = d.is_playing ? '<i class="fa fa-pause"></i>' : '<i class="fa fa-play"></i>';
  } else if (d && d.type === "radio") {
    $("np-thumb").src = "/static/images/radio.png";
    $("np-title").textContent = "Live Radio";
    $("np-artist").textContent = d.station || "";
    $("np-current-time").textContent = msToTime((d.elapsed || 0) * 1000);
    $("np-duration").textContent = ""; $("np-progress").value = 100;
    $("np-play-pause").innerHTML = '<i class="fa fa-pause"></i>';
  } else {
    $("np-thumb").src = "/static/images/radio.png";
    $("np-title").textContent = "No music playing";
    $("np-artist").textContent = "–";
    $("np-current-time").textContent = "0:00"; $("np-duration").textContent = "--:--";
    $("np-progress").value = 0;
    $("np-play-pause").innerHTML = '<i class="fa fa-play"></i>';
  }
}

// ---------- fetchers ----------
async function refreshAll() {
  try {
    const d = await jget("/api/overview");
    renderSystem(d.system);
    renderServices(d.services);
    renderWeather(d.weather);
    renderAgenda(d.calendar);
    renderNotes(d.notes);
    renderNowPlaying(d.now_playing);
  } catch (e) { console.error("overview:", e); }
}
async function refreshNowPlaying() {
  try { renderNowPlaying(await jget("/api/current_playing")); }
  catch (e) { console.error("now_playing:", e); }
}
async function refreshNotes() {
  try { renderNotes((await jget("/api/notes")).notes); }
  catch (e) { console.error(e); }
}

// ---------- controls ----------
$("np-play-pause").addEventListener("click", async () => {
  const url = npState.type === "radio"
    ? (npState.station ? "/api/radio_pause" : "/api/radio_resume")
    : (npState.is_playing ? "/api/spotify_pause" : "/api/spotify_resume");
  await jpost(url); refreshNowPlaying();
});
$("next-btn").addEventListener("click", async () => { await jpost("/api/spotify_next"); refreshNowPlaying(); });
$("prev-btn").addEventListener("click", async () => { await jpost("/api/spotify_previous"); refreshNowPlaying(); });

document.querySelectorAll(".actions button[data-routine]").forEach(btn => {
  btn.addEventListener("click", async () => {
    const msg = $("qa-msg");
    msg.textContent = `${btn.textContent.trim()}…`;
    try {
      const r = await jpost("/api/routines/run", { id: btn.dataset.routine });
      msg.textContent = r.success ? "Klaar." : `Mislukt: ${r.error || "?"}`;
    } catch (e) { msg.textContent = "Mislukt."; }
    setTimeout(() => (msg.textContent = ""), 4000);
  });
});

async function addNote() {
  const inp = $("note-input");
  const text = inp.value.trim();
  if (!text) return;
  await jpost("/api/notes", { note: text });
  inp.value = ""; refreshNotes();
}
$("note-add").addEventListener("click", addNote);
$("note-input").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); addNote(); } });

// ---------- schedule ----------
(async function init() {
  let cfg = { poll: {} };
  try { cfg = await jget("/api/config"); } catch (e) {}
  const p = cfg.poll || {};
  const overviewMs = Math.max(p.poll_system_ms || 10000, 5000);
  const npMs = Math.max(p.poll_now_playing_ms || 4000, 2000);

  await refreshAll();
  setInterval(refreshAll, overviewMs);
  // only add a separate now-playing poll if it's meaningfully faster
  if (npMs < overviewMs - 1000) setInterval(refreshNowPlaying, npMs);
})();
