const j = (u, o) => fetch(u, o).then(r => r.json());

function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast" + (isErr ? " err" : "")), 3000);
}

async function load() {
  const body = document.getElementById("notes-body");
  try {
    const d = await j("/api/notes");
    if (!d.notes || !d.notes.length) {
      body.innerHTML = '<tr><td class="muted">Nog geen notities.</td></tr>';
      return;
    }
    body.innerHTML = d.notes.map((n, i) =>
      `<tr><td>${esc(n)}</td><td style="width:40px;text-align:right">
        <button class="btn small danger" data-del="${i}">×</button></td></tr>`
    ).join("");
    body.querySelectorAll("[data-del]").forEach(b => b.addEventListener("click", async () => {
      await fetch(`/api/notes/${b.dataset.del}`, { method: "DELETE" });
      load();
    }));
  } catch (e) {
    body.innerHTML = '<tr><td class="empty">Kon notities niet laden.</td></tr>';
  }
}

async function add() {
  const inp = document.getElementById("note-input");
  const text = inp.value.trim();
  if (!text) return;
  const r = await j("/api/notes", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ note: text }),
  });
  if (r.success) { inp.value = ""; load(); toast("Notitie toegevoegd"); }
  else toast(r.error || "Mislukt", true);
}

document.getElementById("note-add").addEventListener("click", add);
document.getElementById("note-input").addEventListener("keydown", e => { if (e.key === "Enter") add(); });
load();

// ---- Wekker ----
const aStatus = document.getElementById("alarm-status");
const aTime = document.getElementById("alarm");
const aRoutine = document.getElementById("alarm-routine");

async function loadAlarm() {
  try {
    const [al, rl] = await Promise.all([j("/api/alarm"), j("/api/routines")]);
    aRoutine.innerHTML = (rl.routines || []).map(r =>
      `<option value="${esc(r.id)}" ${r.id === al.routine ? "selected" : ""}>${esc(r.name)}</option>`).join("");
    if (al.set) {
      aStatus.textContent = `Staat aan: ${al.when} → ${al.routine}`;
      aStatus.className = "";
    } else {
      aStatus.textContent = "Geen wekker gezet.";
      aStatus.className = "muted";
    }
    if (al.time) aTime.value = al.time;
  } catch (e) { aStatus.textContent = "Kon wekker-status niet laden."; }
}

document.getElementById("alarm-set").addEventListener("click", async () => {
  if (!aTime.value) { toast("Kies een tijd", true); return; }
  const r = await j("/api/alarm", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ time: aTime.value, routine: aRoutine.value }),
  });
  if (r.success) { toast(`Wekker gezet: ${r.when}`); loadAlarm(); }
  else toast(r.error || "Mislukt", true);
});

document.getElementById("alarm-clear").addEventListener("click", async () => {
  const r = await j("/api/alarm", { method: "DELETE" });
  if (r.success) { toast("Wekker gewist"); loadAlarm(); }
});

loadAlarm();
