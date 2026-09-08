// Devices page — one card per lamp from /api/lamps, with live state from
// /api/lamp/state and controls that address the lamp by its index.

const j = (u, o) => fetch(u, o).then(r => r.json());
const put = (u, body) => fetch(u, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(r => r.json());
const post = (u, body) => fetch(u, { method: "POST", headers: { "Content-Type": "application/json" }, body: body ? JSON.stringify(body) : undefined }).then(r => r.json());

function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast" + (isErr ? " err" : "")), 2800);
}

function hsToHex(h, s) {
  s = (s ?? 100) / 100;
  const k = n => (n + h / 30) % 12;
  const f = n => 0.5 - 0.5 * Math.max(-1, Math.min(k(n) - 3, 9 - k(n), 1)) * s;
  const to = n => Math.round(255 * f(n)).toString(16).padStart(2, "0");
  return `#${to(0)}${to(8)}${to(4)}`;
}

function lampCard(lamp, i) {
  return `
  <div class="card device" data-i="${i}">
    <h3><i class="fa fa-lightbulb"></i> ${lamp.name || "Lamp " + (i + 1)}</h3>
    <p class="muted lamp-ip">${lamp.ip || "geen IP"}</p>

    <label class="toggle" style="margin:6px 0">
      <input type="checkbox" class="l-power"><span class="track"></span>
    </label>
    <span class="l-status muted" style="margin-left:10px">…</span>

    <div class="field"><label>Helderheid <span class="l-bri-val">–</span></label>
      <input type="range" class="l-bri" min="1" max="100" value="50"></div>

    <div class="field"><label>Kleurtemperatuur (K) <span class="l-ct-val">–</span></label>
      <input type="range" class="l-ct" min="2500" max="6500" step="100" value="4000"></div>

    <div class="field row"><label>Kleur</label>
      <input type="color" class="l-color" value="#ffcc00"></div>

    <div class="actions" style="grid-template-columns:repeat(3,1fr);margin-top:6px">
      <button class="btn small l-mode" data-mode="normal">Wit</button>
      <button class="btn small l-mode" data-mode="desk">Bureau</button>
      <button class="btn small l-mode" data-mode="party">Party</button>
    </div>
  </div>`;
}

async function loadState(card, i) {
  const st = card.querySelector(".l-status");
  try {
    const r = await j(`/api/lamp/state?lamp=${i}`);
    if (!r.ok) { st.textContent = "offline"; st.className = "l-status red"; return; }
    const s = r.state;
    card.querySelector(".l-power").checked = !!s.on;
    st.textContent = s.on ? "aan" : "uit";
    st.className = "l-status " + (s.on ? "green" : "muted");
    if (s.brightness != null) {
      card.querySelector(".l-bri").value = s.brightness;
      card.querySelector(".l-bri-val").textContent = s.brightness + "%";
    }
    if (s.color_temp) {
      card.querySelector(".l-ct").value = s.color_temp;
      card.querySelector(".l-ct-val").textContent = s.color_temp + "K";
    }
    if (s.hue != null) card.querySelector(".l-color").value = hsToHex(s.hue, s.saturation);
  } catch (e) {
    st.textContent = "onbereikbaar"; st.className = "l-status red";
  }
}

function wireCard(card, i) {
  const busy = fn => async (...a) => { card.style.opacity = .6; try { await fn(...a); } finally { card.style.opacity = 1; } };

  card.querySelector(".l-power").addEventListener("change", busy(async e => {
    const r = await put(`/api/lamp/${e.target.checked ? "on" : "off"}`, { lamp: i });
    if (r.status !== "ok") toast(r.message || "mislukt", true);
    loadState(card, i);
  }));

  card.querySelector(".l-bri").addEventListener("input", e => card.querySelector(".l-bri-val").textContent = e.target.value + "%");
  card.querySelector(".l-bri").addEventListener("change", busy(async e => {
    const r = await put("/api/lamp/brightness", { lamp: i, brightness: +e.target.value });
    if (r.status !== "ok") toast(r.message || "mislukt", true);
  }));

  card.querySelector(".l-ct").addEventListener("input", e => card.querySelector(".l-ct-val").textContent = e.target.value + "K");
  card.querySelector(".l-ct").addEventListener("change", busy(async e => {
    const r = await put("/api/lamp/colortemp", { lamp: i, color_temp: +e.target.value });
    if (r.status !== "ok") toast(r.message || "mislukt", true);
  }));

  card.querySelector(".l-color").addEventListener("change", busy(async e => {
    const r = await put("/api/lamp/color", { lamp: i, color: e.target.value });
    if (r.status !== "ok") toast(r.message || "mislukt", true);
  }));

  card.querySelectorAll(".l-mode").forEach(btn => btn.addEventListener("click", busy(async () => {
    const r = await post(`/api/lamp/mode/${btn.dataset.mode}`, { lamp: i });
    if (r.status !== "ok") toast(r.message || "mislukt", true);
    loadState(card, i);
  })));
}

async function init() {
  const grid = document.getElementById("lamps-grid");
  let lamps = [];
  try { lamps = (await j("/api/lamps")).lamps || []; } catch (e) {}
  if (!lamps.length) {
    grid.innerHTML = '<p class="empty">Geen lampen. Voeg ze toe via <a href="/settings" style="color:#00bfff">Settings</a>.</p>';
    return;
  }
  grid.innerHTML = lamps.map(lampCard).join("");
  grid.querySelectorAll(".card.device").forEach((card, i) => { wireCard(card, i); loadState(card, i); });
}

// --- thermostat (persist naar settings.json) ---
const thermo = document.getElementById("thermo");
const thermoVal = document.getElementById("thermo-val");
let thermoTimer = null;
async function initThermo() {
  try {
    const s = await j("/api/thermostat");
    thermo.min = s.min; thermo.max = s.max; thermo.value = s.target;
    thermoVal.textContent = s.target + "°C";
  } catch (e) {}
}
thermo.addEventListener("input", () => {
  thermoVal.textContent = thermo.value + "°C";
  clearTimeout(thermoTimer);
  thermoTimer = setTimeout(() => post("/api/thermostat", { target: +thermo.value }), 500);
});
initThermo();

init();
