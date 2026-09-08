// Data-driven Settings UI. Renders sections from SCHEMA, reads current values
// from /api/settings, POSTs a nested patch back.

const SCHEMA = [
  {
    key: "ai", title: "AI / chat", icon: "fa-robot",
    fields: [
      { path: "backend", label: "Backend", type: "select", options: ["ollama", "openai"],
        hint: "ollama = gratis & lokaal. openai vereist OPENAI_API_KEY in .env." },
      { path: "ollama_url", label: "Ollama URL", type: "text" },
      { path: "ollama_model", label: "Ollama model", type: "text",
        hint: "bv. qwen2.5:1.5b (snel op een Pi) of llama3.2:3b (slimmer). Eerst 'ollama pull <model>'." },
      { path: "openai_model", label: "OpenAI model", type: "text" },
      { path: "temperature", label: "Temperature", type: "number", step: "0.1", min: 0, max: 2 },
      { path: "max_history", label: "Max gespreksregels", type: "number", min: 2, max: 100 },
    ],
  },
  {
    key: "tts", title: "Spraak uit (TTS)", icon: "fa-volume-high",
    fields: [
      { path: "backend", label: "Backend", type: "select", options: ["piper", "espeak", "openai", "none"],
        hint: "piper = gratis & lokaal, Nederlandse stem." },
      { path: "piper_model", label: "Piper stem", type: "text",
        hint: "bv. nl_NL-mls_5809-low (klein) of nl_NL-mls_5809-medium. Wordt 1x gedownload." },
      { path: "openai_voice", label: "OpenAI stem", type: "text" },
      { path: "volume", label: "Volume", type: "number", step: "0.05", min: 0, max: 1 },
    ],
  },
  {
    key: "stt", title: "Spraak in (STT)", icon: "fa-microphone",
    fields: [
      { path: "backend", label: "Backend", type: "select", options: ["faster_whisper", "whisper", "openai"],
        hint: "faster_whisper = gratis & lokaal, licht genoeg voor een Pi." },
      { path: "model", label: "Model", type: "select", options: ["tiny", "base", "small", "medium"] },
      { path: "compute_type", label: "Compute type", type: "select", options: ["int8", "int8_float16", "float16", "float32"] },
      { path: "language", label: "Taal", type: "text" },
    ],
  },
  {
    key: "weather", title: "Weer", icon: "fa-cloud-sun",
    fields: [
      { path: "provider", label: "Provider", type: "select", options: ["open-meteo", "openweathermap", "weatherapi"],
        hint: "open-meteo heeft geen API-key nodig. Voor openweathermap/weatherapi vul je de key in onder Inloggegevens." },
      { path: "city", label: "Stad", type: "text" },
      { path: "latitude", label: "Latitude", type: "number", step: "0.001" },
      { path: "longitude", label: "Longitude", type: "number", step: "0.001" },
    ],
  },
  {
    key: "search", title: "Internet zoeken", icon: "fa-magnifying-glass",
    fields: [
      { path: "provider", label: "Provider", type: "select", options: ["duckduckgo", "serper"],
        hint: "duckduckgo heeft geen API-key nodig." },
      { path: "max_results", label: "Max resultaten", type: "number", min: 1, max: 15 },
    ],
  },
  {
    key: "camera", title: "Camera", icon: "fa-video",
    fields: [
      { path: "enabled", label: "Camera aan", type: "bool" },
      { path: "backend", label: "Backend", type: "select", options: ["auto", "opencv", "picamera2"],
        hint: "auto = USB-webcam, val terug op de Pi-lintkabelcamera (picamera2)." },
      { path: "device_index", label: "Device index", type: "number", min: 0, max: 9 },
      { path: "width", label: "Breedte (px)", type: "number", min: 160, max: 1920, step: 16,
        hint: "Lager = minder bandbreedte. 640 is prima op trage wifi." },
      { path: "height", label: "Hoogte (px)", type: "number", min: 120, max: 1080, step: 16 },
      { path: "fps", label: "FPS", type: "number", min: 1, max: 30 },
      { path: "jpeg_quality", label: "JPEG kwaliteit", type: "number", min: 20, max: 95,
        hint: "40–60 is een goede balans voor 15 Mbit/s wifi." },
      { path: "browser_detection", label: "Personendetectie in browser", type: "bool",
        hint: "Laadt ~4 MB TensorFlow.js en belast de CPU. Standaard uit." },
      { path: "detect_threshold", label: "Detectie-drempel", type: "number", step: "0.05", min: 0.1, max: 0.9,
        hint: "Zekerheid om iemand te tellen. 0.35–0.45 pakt wazig IR-/nachtbeeld beter, maar meer valse alarmen." },
      { path: "lamp_quiet_from", label: "Lamp-stil vanaf", type: "text",
        hint: "Detectie zet de lamp NIET automatisch aan tussen deze tijden (HH:MM). Gelijk zetten = altijd toegestaan." },
      { path: "lamp_quiet_to", label: "Lamp-stil tot", type: "text" },
    ],
  },
  {
    key: "dashboard", title: "Dashboard verversen (ms)", icon: "fa-arrows-rotate",
    fields: [
      { path: "poll_now_playing_ms", label: "Now playing", type: "number", min: 1000, step: 500,
        hint: "Hoger = minder verkeer en minder Spotify-API-calls." },
      { path: "poll_devices_ms", label: "Spotify-apparaten", type: "number", min: 2000, step: 1000 },
      { path: "poll_weather_ms", label: "Weer", type: "number", min: 60000, step: 60000 },
      { path: "poll_agenda_ms", label: "Agenda", type: "number", min: 60000, step: 60000 },
      { path: "poll_system_ms", label: "Systeemstatus", type: "number", min: 2000, step: 1000 },
    ],
  },
  {
    key: "features", title: "Onderdelen aan/uit", icon: "fa-toggle-on",
    fields: [
      { path: "voice_assistant", label: "Spraakassistent", type: "bool" },
      { path: "spotify", label: "Spotify", type: "bool" },
      { path: "radio", label: "Radio", type: "bool" },
      { path: "calendar", label: "Agenda", type: "bool" },
      { path: "email", label: "E-mail (logs)", type: "bool" },
      { path: "camera", label: "Beveiligingscamera", type: "bool" },
    ],
  },
  {
    key: "assistant", title: "Assistent", icon: "fa-comment-dots",
    fields: [
      { path: "name", label: "Naam", type: "text" },
      { path: "wake_words", label: "Wake words (komma-gescheiden)", type: "list" },
      { path: "wake_backend", label: "Wake-word backend", type: "select", options: ["auto", "porcupine", "whisper"],
        hint: "auto = Porcupine als 't kan (PICOVOICE_ACCESS_KEY + voice/hey_kamer.ppn), anders whisper op de wake-woorden." },
      { path: "porcupine_keyword", label: "Porcupine .ppn-pad", type: "text" },
      { path: "porcupine_sensitivity", label: "Porcupine gevoeligheid", type: "number", step: "0.05", min: 0, max: 1 },
      { path: "system_prompt", label: "System prompt", type: "textarea" },
    ],
  },
];

let CURRENT = {};

const getPath = (obj, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), obj);

function fieldId(section, path) { return `f_${section}_${path.replace(/\./g, "_")}`; }

function renderField(section, f) {
  const id = fieldId(section, f.path);
  const val = getPath(CURRENT[section] || {}, f.path);
  const hint = f.hint ? `<span class="hint">${f.hint}</span>` : "";

  if (f.type === "bool") {
    return `<div class="field row"><label for="${id}">${f.label}</label>
      <span class="toggle"><input type="checkbox" id="${id}" data-section="${section}" data-path="${f.path}" data-type="bool" ${val ? "checked" : ""}><span class="track"></span></span></div>${hint}`;
  }
  let input;
  if (f.type === "select") {
    input = `<select id="${id}" data-section="${section}" data-path="${f.path}" data-type="text">` +
      f.options.map(o => `<option value="${o}" ${String(val) === String(o) ? "selected" : ""}>${o}</option>`).join("") + `</select>`;
  } else if (f.type === "textarea") {
    input = `<textarea id="${id}" rows="5" data-section="${section}" data-path="${f.path}" data-type="text">${val ?? ""}</textarea>`;
  } else if (f.type === "list") {
    input = `<input type="text" id="${id}" data-section="${section}" data-path="${f.path}" data-type="list" value="${Array.isArray(val) ? val.join(", ") : (val ?? "")}">`;
  } else if (f.type === "number") {
    input = `<input type="number" id="${id}" data-section="${section}" data-path="${f.path}" data-type="number"
      value="${val ?? ""}" ${f.min != null ? `min="${f.min}"` : ""} ${f.max != null ? `max="${f.max}"` : ""} ${f.step != null ? `step="${f.step}"` : ""}>`;
  } else {
    input = `<input type="text" id="${id}" data-section="${section}" data-path="${f.path}" data-type="text" value="${val ?? ""}">`;
  }
  return `<div class="field"><label for="${id}">${f.label}</label>${input}${hint}</div>`;
}

function renderLamps() {
  const lamps = (CURRENT.devices && CURRENT.devices.lamps) || [];
  const rows = lamps.map((l, i) => `
    <div class="field row" data-lamp-row="${i}">
      <input type="text" class="lamp-name" data-i="${i}" value="${l.name || ""}" placeholder="Naam" style="flex:1">
      <input type="text" class="lamp-ip" data-i="${i}" value="${l.ip || ""}" placeholder="192.168.x.x" style="flex:1">
      <button type="button" class="btn small danger" data-remove-lamp="${i}">×</button>
    </div>`).join("");
  return `<div class="card"><h3><i class="fa fa-lightbulb"></i> Lampen (Tapo)</h3>
    <div id="lamp-list">${rows || '<p class="empty">Nog geen lampen.</p>'}</div>
    <button type="button" class="btn small" id="add-lamp"><i class="fa fa-plus"></i> Lamp toevoegen</button></div>`;
}

function render() {
  const form = document.getElementById("settings-form");
  form.innerHTML = SCHEMA.map(sec =>
    `<div class="card"><h3><i class="fa ${sec.icon}"></i> ${sec.title}</h3>
      ${sec.fields.map(f => renderField(sec.key, f)).join("")}</div>`
  ).join("") + renderLamps();

  form.querySelector("#add-lamp")?.addEventListener("click", () => {
    CURRENT.devices = CURRENT.devices || {};
    CURRENT.devices.lamps = collectLamps();
    CURRENT.devices.lamps.push({ name: "Nieuwe lamp", ip: "" });
    render();
  });
  form.querySelectorAll("[data-remove-lamp]").forEach(b => b.addEventListener("click", () => {
    CURRENT.devices.lamps = collectLamps();
    CURRENT.devices.lamps.splice(+b.dataset.removeLamp, 1);
    render();
  }));
}

function collectLamps() {
  const names = [...document.querySelectorAll(".lamp-name")];
  const ips = [...document.querySelectorAll(".lamp-ip")];
  return names.map((n, i) => ({ name: n.value.trim(), ip: (ips[i] || {}).value?.trim() || "" }))
    .filter(l => l.name || l.ip);
}

function buildPatch() {
  const patch = {};
  document.querySelectorAll("#settings-form [data-path]").forEach(el => {
    const sec = el.dataset.section, path = el.dataset.path, type = el.dataset.type;
    let v;
    if (type === "bool") v = el.checked;
    else if (type === "number") { v = el.value === "" ? null : Number(el.value); if (v == null || Number.isNaN(v)) return; }
    else if (type === "list") v = el.value.split(",").map(s => s.trim()).filter(Boolean);
    else v = el.value;
    patch[sec] = patch[sec] || {};
    const parts = path.split(".");
    let node = patch[sec];
    for (let i = 0; i < parts.length - 1; i++) node = node[parts[i]] = node[parts[i]] || {};
    node[parts.at(-1)] = v;
  });
  patch.devices = { lamps: collectLamps() };
  return patch;
}

function toast(msg, isErr) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  setTimeout(() => (t.className = "toast" + (isErr ? " err" : "")), 3000);
}

async function load() {
  const d = await fetch("/api/settings").then(r => r.json());
  CURRENT = d.settings || {};
  render();
  const errs = Object.entries(d.errors || {}).filter(([, v]) => v);
  if (errs.length) toast(`Let op: ${errs.map(([k]) => k).join(", ")} gaf een fout`, true);
}

async function save() {
  const msg = document.getElementById("save-msg");
  msg.textContent = "Opslaan…"; msg.className = "save-msg";
  try {
    const r = await fetch("/api/settings", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPatch()),
    }).then(r => r.json());
    if (r.success) {
      CURRENT = r.settings; render();
      msg.textContent = "Opgeslagen ✓"; msg.className = "save-msg ok";
      toast("Instellingen opgeslagen en toegepast");
    } else {
      msg.textContent = r.error || "Mislukt"; msg.className = "save-msg err";
    }
  } catch (e) {
    msg.textContent = "Netwerkfout"; msg.className = "save-msg err";
  }
  setTimeout(() => (msg.textContent = ""), 4000);
}

document.getElementById("save-btn").addEventListener("click", save);
document.getElementById("save-btn2").addEventListener("click", save);
document.getElementById("reload-btn").addEventListener("click", load);
load();

// =====================================================================
// Inloggegevens (secrets) — ontgrendelen met een code per mail
// =====================================================================
let UNLOCK_TOKEN = null;

const SECRET_GROUPS = [
  { title: "Dashboard-beveiliging", icon: "fa-shield-halved", keys: [
      ["DASHBOARD_PASSWORD", "Wachtwoord (leeg = geen slot; beschermt camera/chat/routines/settings)", "password"]] },
  { title: "OpenAI (optioneel)", icon: "fa-robot", keys: [["OPENAI_API_KEY", "API-key", "password"]] },
  { title: "Lampen (Tapo)", icon: "fa-lightbulb", keys: [
      ["TAPO_USER", "TP-Link e-mail", "text"], ["TAPO_PASSWORD", "TP-Link wachtwoord", "password"]] },
  { title: "E-mail (Gmail)", icon: "fa-envelope", keys: [
      ["EMAIL_ADDRESS", "Afzender-adres", "text"], ["EMAIL_PASSWORD", "App-wachtwoord", "password"],
      ["SMTP_SERVER", "SMTP-server", "text"], ["SMTP_PORT", "Poort", "text"],
      ["RECEIVER", "Ontvanger (jij)", "text"]] },
  { title: "Apple agenda (CalDAV)", icon: "fa-calendar", keys: [
      ["APPLE_ID_1", "Apple ID", "text"], ["APPLE_PASSWORD_1", "App-wachtwoord", "password"],
      ["APPLE_ID_2", "Apple ID 2 (optioneel)", "text"], ["APPLE_PASSWORD_2", "App-wachtwoord 2", "password"]] },
  { title: "Spotify", icon: "fa-music", keys: [
      ["SPOTIFY_CLIENT_ID", "Client ID", "text"], ["SPOTIFY_CLIENT_SECRET", "Client Secret", "password"],
      ["SPOTIFY_REDIRECT_URI", "Redirect URI", "text"]], spotify: true },
  { title: "Weer & zoeken (optioneel)", icon: "fa-key", keys: [
      ["OPENWEATHER_KEY", "OpenWeatherMap-key", "password"],
      ["WEATHERAPI_KEY", "WeatherAPI-key", "password"],
      ["SERPER_API_KEY", "Serper-key", "password"]] },
  { title: "Wake word (optioneel)", icon: "fa-microphone-lines", keys: [
      ["PICOVOICE_ACCESS_KEY", "Picovoice AccessKey (console.picovoice.ai)", "password"]] },
];

const secBox = document.getElementById("secrets-section");

async function loadSecrets() {
  const headers = UNLOCK_TOKEN ? { "X-Unlock-Token": UNLOCK_TOKEN } : {};
  let d;
  try { d = await fetch("/api/secrets", { headers }).then(r => r.json()); }
  catch (e) { secBox.innerHTML = '<div class="card"><p class="empty">Kon secrets-status niet laden.</p></div>'; return; }
  if (UNLOCK_TOKEN && !d.unlocked) UNLOCK_TOKEN = null;  // sessie verlopen
  renderSecrets(d);
}

function lockedCard(d) {
  const mailNote = d.mail_ready
    ? "Je krijgt een code per mail op je RECEIVER-adres."
    : "<b>Mail nog niet ingesteld.</b> Vul EMAIL_ADDRESS, EMAIL_PASSWORD en RECEIVER eerst eenmalig in via het .env-bestand op de Pi.";
  return `
  <div class="card">
    <h3><i class="fa fa-lock"></i> Inloggegevens</h3>
    <p class="muted">API-keys en wachtwoorden. Achter een slot; waarden worden nooit teruggestuurd naar de browser.</p>
    <p class="muted">${mailNote}</p>
    <button class="btn" id="req-code" ${d.mail_ready ? "" : "disabled"}><i class="fa fa-paper-plane"></i> Stuur code naar mail</button>
    <div id="code-row" hidden style="margin-top:12px">
      <div class="field row" style="max-width:320px">
        <input type="text" id="code-input" inputmode="numeric" maxlength="6" placeholder="6-cijferige code">
        <button class="btn primary" id="code-verify">Ontgrendel</button>
      </div>
    </div>
    <p class="save-msg" id="sec-msg"></p>
  </div>`;
}

function field(key, label, type, s) {
  const ph = s.set ? (s.hint || "••••••") : "niet ingesteld";
  return `<div class="field"><label>${label}</label>
    <input type="${type === "password" ? "password" : "text"}" data-secret="${key}"
      placeholder="${ph}" autocomplete="off"></div>`;
}

function unlockedCards(d) {
  const cards = SECRET_GROUPS.map(g => `
    <div class="card">
      <h3><i class="fa ${g.icon}"></i> ${g.title}</h3>
      ${g.keys.map(([k, l, t]) => field(k, l, t, d.secrets[k] || {})).join("")}
      ${g.spotify ? `
      <button class="btn small" id="sp-connect" style="margin-top:6px"><i class="fa fa-link"></i> Verbind met Spotify</button>
      <div id="sp-row" hidden style="margin-top:10px">
        <p class="muted">Open de link, log in bij Spotify, en plak hieronder de URL waar je op uitkwam.</p>
        <div class="field"><input type="text" id="sp-redirect" placeholder="http://127.0.0.1:8000/callback?code=..."></div>
        <button class="btn small primary" id="sp-finish">Koppelen afronden</button>
      </div>` : ""}
    </div>`).join("");
  return `
  <div class="card" style="grid-column:1/-1">
    <h3><i class="fa fa-lock-open"></i> Inloggegevens <span class="muted">— ontgrendeld (30 min)</span></h3>
    <p class="muted">Laat een veld leeg om het ongewijzigd te laten. Leegmaken kan door een spatie in te vullen en op te slaan.</p>
  </div>
  <div class="settings-grid" style="grid-column:1/-1">${cards}</div>
  <div class="settings-actions" style="grid-column:1/-1">
    <button class="btn primary" id="sec-save"><i class="fa fa-floppy-disk"></i> Inloggegevens opslaan</button>
    <span class="save-msg" id="sec-save-msg"></span>
  </div>`;
}

function renderSecrets(d) {
  if (!d.unlocked || !UNLOCK_TOKEN) {
    secBox.className = "";
    secBox.innerHTML = lockedCard(d);
    wireLock(d);
  } else {
    secBox.className = "settings-grid";
    secBox.innerHTML = unlockedCards(d);
    wireUnlocked();
  }
}

function wireLock(d) {
  const msg = document.getElementById("sec-msg");
  const req = document.getElementById("req-code");
  if (req) req.addEventListener("click", async () => {
    req.disabled = true; msg.textContent = "Versturen…"; msg.className = "save-msg";
    try {
      const r = await fetch("/api/auth/request-code", { method: "POST" }).then(r => r.json());
      if (r.ok) {
        msg.textContent = "Code gemaild ✓"; msg.className = "save-msg ok";
        document.getElementById("code-row").hidden = false;
        document.getElementById("code-input").focus();
      } else { msg.textContent = r.error || "Mislukt"; msg.className = "save-msg err"; req.disabled = false; }
    } catch (e) { msg.textContent = "Netwerkfout"; msg.className = "save-msg err"; req.disabled = false; }
  });
  const verify = document.getElementById("code-verify");
  if (verify) verify.addEventListener("click", async () => {
    const code = document.getElementById("code-input").value.trim();
    if (!code) return;
    msg.textContent = "Controleren…"; msg.className = "save-msg";
    try {
      const r = await fetch("/api/auth/verify", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      }).then(r => r.json());
      if (r.ok) { UNLOCK_TOKEN = r.token; toast("Ontgrendeld"); loadSecrets(); }
      else { msg.textContent = r.error || "Ongeldig"; msg.className = "save-msg err"; }
    } catch (e) { msg.textContent = "Netwerkfout"; msg.className = "save-msg err"; }
  });
}

function wireUnlocked() {
  document.getElementById("sec-save").addEventListener("click", async () => {
    const msg = document.getElementById("sec-save-msg");
    const payload = {};
    secBox.querySelectorAll("input[data-secret]").forEach(inp => {
      if (inp.value !== "") payload[inp.dataset.secret] = inp.value;
    });
    if (!Object.keys(payload).length) { msg.textContent = "Niks gewijzigd"; return; }
    msg.textContent = "Opslaan…"; msg.className = "save-msg";
    try {
      const r = await fetch("/api/secrets", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Unlock-Token": UNLOCK_TOKEN },
        body: JSON.stringify(payload),
      }).then(r => r.json());
      if (r.ok) {
        msg.textContent = `Opgeslagen: ${r.changed.join(", ")} ✓`; msg.className = "save-msg ok";
        toast("Inloggegevens opgeslagen en toegepast");
        loadSecrets();
      } else {
        if (r.error === "Niet ontgrendeld") { UNLOCK_TOKEN = null; loadSecrets(); }
        msg.textContent = r.error || "Mislukt"; msg.className = "save-msg err";
      }
    } catch (e) { msg.textContent = "Netwerkfout"; msg.className = "save-msg err"; }
  });

  const spc = document.getElementById("sp-connect");
  if (spc) spc.addEventListener("click", async () => {
    const r = await fetch("/api/spotify/auth-url", { headers: { "X-Unlock-Token": UNLOCK_TOKEN } }).then(r => r.json());
    if (!r.ok) { toast(r.error || "Vul eerst client-id/secret in en sla op", true); return; }
    window.open(r.url, "_blank", "noopener");
    document.getElementById("sp-row").hidden = false;
  });
  const spf = document.getElementById("sp-finish");
  if (spf) spf.addEventListener("click", async () => {
    const redirect_url = document.getElementById("sp-redirect").value.trim();
    if (!redirect_url) return;
    const r = await fetch("/api/spotify/token", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Unlock-Token": UNLOCK_TOKEN },
      body: JSON.stringify({ redirect_url }),
    }).then(r => r.json());
    toast(r.ok ? "Spotify gekoppeld ✓" : (r.error || "Koppelen mislukt"), !r.ok);
    if (r.ok) document.getElementById("sp-row").hidden = true;
  });
}

loadSecrets();
