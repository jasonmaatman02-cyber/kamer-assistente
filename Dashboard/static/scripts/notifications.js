const j = (u, ms = 8000) => {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return fetch(u, { signal: c.signal }).then(r => r.json()).finally(() => clearTimeout(t));
};

let inflight = false;

async function load() {
  if (inflight) return;
  inflight = true;
  const body = document.getElementById("log-body");
  try {
    const d = await j("/api/notifications?limit=60");
    document.getElementById("date").textContent = d.date ? `— ${d.date}` : "";
    if (!d.notifications || !d.notifications.length) {
      body.innerHTML = '<tr><td colspan="3" class="empty">Geen recente gebeurtenissen.</td></tr>';
      return;
    }
    body.innerHTML = d.notifications.map(n =>
      `<tr><td class="muted">${n.time || ""}</td><td>${n.subject || ""}</td><td>${n.message || ""}</td></tr>`
    ).join("");
  } catch (e) {
    body.innerHTML = '<tr><td colspan="3" class="empty">Kon logboek niet laden.</td></tr>';
  } finally {
    inflight = false;
  }
}

document.getElementById("refresh").addEventListener("click", load);
load();
setInterval(load, 30000);
