const j = (u, o) => fetch(u, o).then(r => r.json());

function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast" + (isErr ? " err" : "")), 3500);
}

const ICONS = { morning: "fa-sun", bedtime: "fa-moon", party: "fa-champagne-glasses", desk: "fa-lightbulb" };

async function load() {
  const grid = document.getElementById("routines-grid");
  try {
    const d = await j("/api/routines");
    grid.innerHTML = d.routines.map(r => `
      <div class="card">
        <h3><i class="fa ${ICONS[r.id] || "fa-bolt"}"></i> ${r.name}</h3>
        <p class="muted">${r.desc}</p>
        <button class="btn primary" data-run="${r.id}"><i class="fa fa-play"></i> Nu uitvoeren</button>
      </div>`).join("");
    grid.querySelectorAll("[data-run]").forEach(btn => btn.addEventListener("click", async () => {
      btn.disabled = true;
      const original = btn.innerHTML;
      btn.innerHTML = '<i class="fa fa-spinner fa-pulse"></i> Bezig…';
      try {
        const r = await j("/api/routines/run", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ id: btn.dataset.run }),
        });
        toast(r.success ? "Routine uitgevoerd" : `Mislukt: ${r.error || "?"}`, !r.success);
      } catch (e) { toast("Netwerkfout", true); }
      btn.disabled = false;
      btn.innerHTML = original;
    }));
  } catch (e) {
    grid.innerHTML = '<p class="empty">Kon routines niet laden.</p>';
  }
}
load();
