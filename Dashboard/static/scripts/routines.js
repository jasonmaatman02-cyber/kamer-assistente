const j = (u, o) => fetch(u, o).then(r => r.json());
const $ = id => document.getElementById(id);

function toast(msg, isErr) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast" + (isErr ? " err" : "")), 3500);
}

const ICONS = { morning: "fa-sun", bedtime: "fa-moon", party: "fa-champagne-glasses", desk: "fa-lightbulb" };

async function load() {
  const grid = $("routines-grid");
  try {
    const d = await j("/api/routines");
    grid.innerHTML = d.routines.map(r => `
      <div class="card">
        <h3><i class="fa ${ICONS[r.id] || "fa-bolt"}"></i> ${esc(r.name)}</h3>
        <p class="muted">${esc(r.desc || "")}</p>
        <button class="btn primary" data-run="${esc(r.id)}"><i class="fa fa-play"></i> Nu uitvoeren</button>
        ${r.builtin ? "" : `<button class="btn small danger" data-del="${esc(r.id)}" style="margin-left:6px"><i class="fa fa-trash"></i></button>`}
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
    grid.querySelectorAll("[data-del]").forEach(btn => btn.addEventListener("click", async () => {
      if (!confirm(`Routine "${btn.dataset.del}" verwijderen?`)) return;
      await j(`/api/routines/${btn.dataset.del}`, { method: "DELETE" });
      load();
    }));
  } catch (e) {
    grid.innerHTML = '<p class="empty">Kon routines niet laden.</p>';
  }
}

// ---------------- builder ----------------
const STEP_HTML = `
  <div class="field row step" style="gap:8px;flex-wrap:wrap">
    <select class="s-action">
      <option value="lamp">Lamp</option>
      <option value="radio">Radio</option>
      <option value="spotify">Spotify-playlist</option>
      <option value="say">Zeg iets</option>
    </select>
    <span class="s-params" style="display:flex;gap:8px;flex:1;flex-wrap:wrap"></span>
    <button type="button" class="btn small danger s-del">×</button>
  </div>`;

function paramsFor(action) {
  if (action === "lamp") return `<input type="number" class="p-lamp" value="0" min="0" style="width:60px" title="lamp #">
    <select class="p-mode"><option>on</option><option>off</option><option>desk</option><option>party</option><option>normal</option></select>`;
  if (action === "radio") return `<input type="text" class="p-station" value="radio538" placeholder="station">`;
  if (action === "spotify") return `<input type="text" class="p-playlist" placeholder="playlist ID">`;
  if (action === "say") return `<input type="text" class="p-text" placeholder="tekst" style="flex:1">`;
  return "";
}

function addStep() {
  const wrap = document.createElement("div");
  wrap.innerHTML = STEP_HTML;
  const row = wrap.firstElementChild;
  const sel = row.querySelector(".s-action");
  const p = row.querySelector(".s-params");
  const render = () => (p.innerHTML = paramsFor(sel.value));
  sel.addEventListener("change", render);
  render();
  row.querySelector(".s-del").addEventListener("click", () => row.remove());
  $("steps").appendChild(row);
}

function collectSteps() {
  return [...document.querySelectorAll("#steps .step")].map(row => {
    const a = row.querySelector(".s-action").value;
    if (a === "lamp") return { action: "lamp", lamp: +row.querySelector(".p-lamp").value, mode: row.querySelector(".p-mode").value };
    if (a === "radio") return { action: "radio", station: row.querySelector(".p-station").value.trim() };
    if (a === "spotify") return { action: "spotify", playlist_id: row.querySelector(".p-playlist").value.trim() };
    if (a === "say") return { action: "say", text: row.querySelector(".p-text").value };
    return null;
  }).filter(Boolean);
}

$("new-routine").addEventListener("click", () => {
  $("builder").hidden = false;
  $("steps").innerHTML = "";
  addStep();
  $("r-name").value = ""; $("r-desc").value = "";
  $("builder").scrollIntoView({ behavior: "smooth" });
});
$("cancel-routine").addEventListener("click", () => ($("builder").hidden = true));
$("add-step").addEventListener("click", addStep);
$("save-routine").addEventListener("click", async () => {
  const name = $("r-name").value.trim();
  const msg = $("r-msg");
  if (!name) { msg.textContent = "Naam vereist"; msg.className = "save-msg err"; return; }
  const steps = collectSteps();
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || ("r" + Date.now());
  msg.textContent = "Opslaan…"; msg.className = "save-msg";
  const r = await j("/api/routines", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, name, desc: $("r-desc").value.trim(), steps }),
  });
  if (r.success) {
    msg.textContent = ""; $("builder").hidden = true;
    toast("Routine opgeslagen"); load();
  } else {
    msg.textContent = r.error || "Mislukt"; msg.className = "save-msg err";
  }
});

load();
