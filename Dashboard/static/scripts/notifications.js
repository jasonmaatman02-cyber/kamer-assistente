const j = (u, ms = 8000) => {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return fetch(u, { signal: c.signal }).then(r => r.json()).finally(() => clearTimeout(t));
};
const $ = id => document.getElementById(id);
const SEEN_KEY = "kamer_notif_seen";

let ALL = [];
let inflight = false;

function lastSeen() {
  return localStorage.getItem(SEEN_KEY) || "";
}
function keyOf(n) { return `${n.time}|${n.subject}|${n.message}`.slice(0, 120); }

function render() {
  const body = $("log-body");
  const sub = $("f-subject").value;
  const q = $("f-text").value.trim().toLowerCase();
  const seen = lastSeen();
  const rows = ALL.filter(n =>
    (!sub || n.subject === sub) &&
    (!q || (n.message + " " + n.subject).toLowerCase().includes(q))
  );
  if (!rows.length) {
    body.innerHTML = '<tr><td colspan="3" class="empty">Niks gevonden.</td></tr>';
    return;
  }
  body.innerHTML = rows.map(n => {
    const isNew = seen && keyOf(n) > seen;
    return `<tr${isNew ? ' style="background:#16202f"' : ""}>
      <td class="muted">${n.time || ""}${isNew ? ' <i class="fa fa-circle" style="color:#00bfff;font-size:7px"></i>' : ""}</td>
      <td>${n.subject || ""}</td><td>${n.message || ""}</td></tr>`;
  }).join("");
}

async function load() {
  if (inflight) return;
  inflight = true;
  try {
    const d = await j("/api/notifications?limit=100");
    $("date").textContent = d.date ? `— ${d.date}` : "";
    ALL = d.notifications || [];
    // onderwerp-dropdown vullen
    const subs = [...new Set(ALL.map(n => n.subject).filter(Boolean))].sort();
    const sel = $("f-subject");
    const cur = sel.value;
    sel.innerHTML = '<option value="">Alle onderwerpen</option>' +
      subs.map(s => `<option${s === cur ? " selected" : ""}>${s}</option>`).join("");
    // nieuw sinds vorige keer
    const seen = lastSeen();
    const n = seen ? ALL.filter(x => keyOf(x) > seen).length : 0;
    $("new-count").textContent = n ? `${n} nieuw` : "";
    render();
  } catch (e) {
    $("log-body").innerHTML = '<tr><td colspan="3" class="empty">Kon logboek niet laden.</td></tr>';
  } finally {
    inflight = false;
  }
}

$("refresh").addEventListener("click", load);
$("f-subject").addEventListener("change", render);
$("f-text").addEventListener("input", render);
$("mark-read").addEventListener("click", () => {
  if (ALL.length) localStorage.setItem(SEEN_KEY, keyOf(ALL[0]));  // nieuwste staat bovenaan
  $("new-count").textContent = "";
  render();
});

load();
setInterval(load, 30000);
