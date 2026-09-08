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
      `<tr><td>${n}</td><td style="width:40px;text-align:right">
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
