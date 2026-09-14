// Kalender-tab. Praat uitsluitend met GET /api/calendar/events -- geen
// Google/iCloud-specifieke logica hier, alleen het platte, uniforme
// event-formaat dat de backend levert (zie scheduler/agenda.py::_normalize_event).

const $ = (id) => document.getElementById(id);

const MAANDEN = ["januari", "februari", "maart", "april", "mei", "juni", "juli",
  "augustus", "september", "oktober", "november", "december"];
const DAGEN_KORT = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"];
const DAGEN_LANG = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"];

let view = "month";
let anchor = new Date();   // "waar we naar kijken" -- vandaag bij het laden
anchor.setHours(0, 0, 0, 0);

// -------------------------------------------------------------------- //
// Datum-hulpjes (puur vanilla JS, geen extra library nodig)
// -------------------------------------------------------------------- //
function addDays(d, n) { const r = new Date(d); r.setDate(r.getDate() + n); return r; }
function addMonths(d, n) { const r = new Date(d); r.setMonth(r.getMonth() + n); return r; }
function startOfWeek(d) {   // maandag als eerste dag
  const r = new Date(d);
  const dow = (r.getDay() + 6) % 7;   // 0=maandag
  r.setDate(r.getDate() - dow);
  return r;
}
function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}
function dateKey(d) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
function toIsoDate(d) { return dateKey(d); }
function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}
function fmtFullDate(d) {
  return `${DAGEN_LANG[(d.getDay() + 6) % 7]} ${d.getDate()} ${MAANDEN[d.getMonth()]} ${d.getFullYear()}`;
}

// -------------------------------------------------------------------- //
// Bereik + label per weergave
// -------------------------------------------------------------------- //
function rangeForView() {
  if (view === "month") {
    const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
    const gridStart = startOfWeek(first);
    const gridEnd = addDays(gridStart, 42);   // 6 weken, ruim genoeg voor de query
    return { start: gridStart, end: gridEnd, label: `${MAANDEN[anchor.getMonth()]} ${anchor.getFullYear()}` };
  }
  if (view === "week") {
    const start = startOfWeek(anchor);
    const end = addDays(start, 7);
    const last = addDays(start, 6);
    const label = start.getMonth() === last.getMonth()
      ? `${start.getDate()}–${last.getDate()} ${MAANDEN[start.getMonth()]} ${start.getFullYear()}`
      : `${start.getDate()} ${MAANDEN[start.getMonth()]} – ${last.getDate()} ${MAANDEN[last.getMonth()]} ${last.getFullYear()}`;
    return { start, end, label };
  }
  // day
  const start = new Date(anchor);
  const end = addDays(start, 1);
  return { start, end, label: fmtFullDate(start) };
}

// -------------------------------------------------------------------- //
// Data ophalen
// -------------------------------------------------------------------- //
async function fetchEvents(start, end) {
  const url = `/api/calendar/events?start=${toIsoDate(start)}&end=${toIsoDate(end)}`;
  try {
    const r = await fetch(url);
    const d = await r.json();
    return d;
  } catch (e) {
    return { events: [], error: "Kon agenda niet laden (netwerkfout)" };
  }
}

// -------------------------------------------------------------------- //
// Renderen
// -------------------------------------------------------------------- //
function groupByDay(events) {
  const map = new Map();
  for (const ev of events) {
    if (!ev.start) continue;
    const key = dateKey(new Date(ev.start));
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(ev);
  }
  for (const list of map.values()) {
    list.sort((a, b) => (a.all_day === b.all_day ? (a.start || "").localeCompare(b.start || "") : a.all_day ? -1 : 1));
  }
  return map;
}

function pillHtml(ev) {
  const time = ev.all_day ? "" : `<span class="cal-pill-time">${fmtTime(ev.start)}</span>`;
  return `<button class="cal-event-pill provider-${esc(ev.provider)}" data-id="${esc(ev.id)}">${time}${esc(ev.title)}</button>`;
}

function renderMonth(events) {
  const byDay = groupByDay(events);
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const gridStart = startOfWeek(first);
  const today = new Date(); today.setHours(0, 0, 0, 0);

  let html = DAGEN_KORT.map(d => `<div class="cal-month-head">${d}</div>`).join("");
  for (let i = 0; i < 42; i++) {
    const d = addDays(gridStart, i);
    const key = dateKey(d);
    const dayEvents = byDay.get(key) || [];
    const otherMonth = d.getMonth() !== anchor.getMonth();
    const isToday = sameDay(d, today);
    const shown = dayEvents.slice(0, 3);
    const rest = dayEvents.length - shown.length;
    html += `<div class="cal-month-cell${otherMonth ? " other-month" : ""}${isToday ? " today" : ""}">
      <span class="cal-month-daynum">${d.getDate()}</span>
      ${shown.map(pillHtml).join("")}
      ${rest > 0 ? `<span class="cal-month-more" data-day="${key}">+${rest} meer</span>` : ""}
    </div>`;
  }
  $("cal-body").innerHTML = `<div class="cal-month-grid">${html}</div>`;
}

function renderAgenda(events, days) {
  const byDay = groupByDay(events);
  let html = '<div class="cal-agenda-list">';
  for (const d of days) {
    const key = dateKey(d);
    const dayEvents = byDay.get(key) || [];
    html += `<div class="cal-day-group">
      <h4>${DAGEN_LANG[(d.getDay() + 6) % 7]} ${d.getDate()} ${MAANDEN[d.getMonth()]}</h4>
      ${dayEvents.length ? dayEvents.map(ev => `
        <div class="cal-event-row provider-${esc(ev.provider)}" data-id="${esc(ev.id)}">
          <span class="cal-row-time">${ev.all_day ? "Hele dag" : `${fmtTime(ev.start)}–${fmtTime(ev.end)}`}</span>
          <span class="cal-row-title">${esc(ev.title)}</span>
          <span class="cal-row-cal">${esc(ev.calendar)}</span>
        </div>`).join("") : '<p class="muted">Geen events.</p>'}
    </div>`;
  }
  html += "</div>";
  $("cal-body").innerHTML = html;
}

let _lastEvents = [];

async function render() {
  const { start, end, label } = rangeForView();
  $("cal-range-label").textContent = label;
  document.querySelectorAll("#cal-view-switch .btn").forEach(b => b.classList.toggle("active", b.dataset.view === view));

  $("cal-body").innerHTML = '<p class="muted"><i class="fa fa-spinner fa-pulse"></i> laden…</p>';
  const { events, error } = await fetchEvents(start, end);
  _lastEvents = events || [];

  const errEl = $("cal-error");
  if (error) { errEl.textContent = "Let op: " + error; errEl.hidden = false; }
  else { errEl.hidden = true; }

  if (view === "month") {
    renderMonth(_lastEvents);
  } else if (view === "week") {
    const wkStart = startOfWeek(anchor);
    renderAgenda(_lastEvents, [0, 1, 2, 3, 4, 5, 6].map(n => addDays(wkStart, n)));
  } else {
    renderAgenda(_lastEvents, [new Date(anchor)]);
  }
}

// -------------------------------------------------------------------- //
// Event-detailmodal
// -------------------------------------------------------------------- //
function openEventModal(id) {
  const ev = _lastEvents.find(e => String(e.id) === id);
  if (!ev) return;
  $("cal-modal-title").textContent = ev.title || "Geen titel";
  const start = ev.start ? new Date(ev.start) : null;
  $("cal-modal-date").textContent = start ? fmtFullDate(start) : "onbekend";
  $("cal-modal-time").textContent = ev.all_day ? "Hele dag" : `${fmtTime(ev.start)} – ${fmtTime(ev.end)}`;
  $("cal-modal-calendar").textContent = `${ev.calendar || "?"} (${ev.provider || "?"})`;
  const locRow = $("cal-modal-location-row");
  if (ev.location) { $("cal-modal-location").textContent = ev.location; locRow.hidden = false; }
  else { locRow.hidden = true; }
  $("cal-modal-description").textContent = ev.description || "";
  $("cal-modal").hidden = false;
}
function closeEventModal() { $("cal-modal").hidden = true; }

// -------------------------------------------------------------------- //
// Init + events
// -------------------------------------------------------------------- //
$("cal-body").addEventListener("click", (e) => {
  const pill = e.target.closest("[data-id]");
  if (pill) { openEventModal(pill.dataset.id); return; }
  const more = e.target.closest(".cal-month-more");
  if (more) {
    const [y, m, d] = more.dataset.day.split("-").map(Number);
    anchor = new Date(y, m - 1, d);
    view = "day";
    render();
  }
});
$("cal-modal-close").addEventListener("click", closeEventModal);
$("cal-modal").addEventListener("click", (e) => { if (e.target.id === "cal-modal") closeEventModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeEventModal(); });

$("cal-view-switch").addEventListener("click", (e) => {
  const btn = e.target.closest("[data-view]");
  if (!btn) return;
  view = btn.dataset.view;
  render();
});
$("cal-today").addEventListener("click", () => { anchor = new Date(); anchor.setHours(0, 0, 0, 0); render(); });
$("cal-prev").addEventListener("click", () => {
  anchor = view === "month" ? addMonths(anchor, -1) : view === "week" ? addDays(anchor, -7) : addDays(anchor, -1);
  render();
});
$("cal-next").addEventListener("click", () => {
  anchor = view === "month" ? addMonths(anchor, 1) : view === "week" ? addDays(anchor, 7) : addDays(anchor, 1);
  render();
});

render();
