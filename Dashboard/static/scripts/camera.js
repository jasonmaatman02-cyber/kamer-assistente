// Security camera view.
// Browser-side person detection (TensorFlow.js + coco-ssd) is OFF by default and
// only runs when Settings -> Camera -> "Personendetectie in browser" aan staat.
// De detectie draait in de browser die deze pagina bekijkt (jouw laptop/telefoon),
// niet op de Pi.

const img = document.querySelector(".camera-feed");
const canvas = document.getElementById("canvas");
const statusEl = document.getElementById("camera-status");

function setStatus(html) { if (statusEl) statusEl.innerHTML = "Status: " + html; }

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const fail = () => reject(new Error("kan niet laden: " + src.split("/").pop()));
    let s = [...document.scripts].find(x => x.src === src);
    if (s) {                              // al bezig/geladen -> niet nog een tag toevoegen
      if (s.dataset.loaded) return resolve();
      s.addEventListener("load", () => resolve());
      s.addEventListener("error", fail);
      return;
    }
    s = document.createElement("script");
    s.src = src;
    s.onload = () => { s.dataset.loaded = "1"; resolve(); };
    s.onerror = fail;
    document.head.appendChild(s);
  });
}

// ---- lamp aansturen, met een kleine cooldown zodat we 'm niet spammen ----
let lampBusy = false;
function callAPI(url) {
  if (lampBusy) return;
  lampBusy = true;
  fetch(url, { method: "PUT" }).catch(e => console.error("lamp API:", e));
  setTimeout(() => (lampBusy = false), 3000);
}

const toMin = s => {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(s || "").trim());
  return m ? (+m[1] % 24) * 60 + (+m[2] % 60) : null;
};
let QUIET = { from: 21 * 60 + 40, to: 7 * 60 };   // stil-venster (lamp niet automatisch aan)
function timeAllowsLamp() {
  if (QUIET.from == null || QUIET.to == null || QUIET.from === QUIET.to) return true;
  const m = new Date().getHours() * 60 + new Date().getMinutes();
  return QUIET.from < QUIET.to
    ? !(m >= QUIET.from && m < QUIET.to)
    : !(m >= QUIET.from || m < QUIET.to);
}

// --------------------------------------------------------------------------- //
// model laden (1x, gepind op een werkende combinatie)
// --------------------------------------------------------------------------- //
const CDN = "https://cdn.jsdelivr.net/npm";
let _modelPromise = null;
function getModel() {
  if (!_modelPromise) {
    _modelPromise = (async () => {
      await loadScript(`${CDN}/@tensorflow/tfjs@4.22.0/dist/tf.min.js`);
      await loadScript(`${CDN}/@tensorflow-models/coco-ssd@2.2.3/dist/coco-ssd.min.js`);
      try { await tf.setBackend("webgl"); } catch (_) { /* val terug op wat er is */ }
      await tf.ready();
      return cocoSsd.load({ base: "lite_mobilenet_v2" });   // lichtste variant
    })().catch(err => { _modelPromise = null; throw err; });
  }
  return _modelPromise;
}

// --------------------------------------------------------------------------- //
// detectie-lus
// --------------------------------------------------------------------------- //
let detector = null;   // { stop() } zolang detectie loopt
let _modelFailAt = 0;  // na een mislukte modellaad niet elke 15s opnieuw hameren

function startDetection(threshold) {
  if (detector) return;
  let stopped = false, paused = false;
  const octx = canvas.getContext("2d");
  let model = null, lampOn = false, lastPerson = Date.now(), missing = 0;

  // op de achtergrond niet scannen (batterij/CPU); meteen weer bij terugkomst
  const onVis = () => {
    if (!document.hidden && paused && !stopped) { paused = false; tick(); }
  };
  document.addEventListener("visibilitychange", onVis);

  detector = {
    stop() {
      stopped = true;
      document.removeEventListener("visibilitychange", onVis);
      try { octx.clearRect(0, 0, canvas.width, canvas.height); } catch (_) {}
      detector = null;
    },
  };

  setStatus('<span class="orange">AI-model laden…</span>');
  getModel().then(m => {
    if (stopped) return;
    model = m;
    setStatus('<span class="green">model geladen</span>');
    tick();
  }).catch(e => {
    setStatus(`<span class="red">model laden mislukt: ${e.message} — opnieuw over 1 min</span>`);
    _modelFailAt = Date.now();
    detector = null;
  });

  function schedule(ms) { if (!stopped) setTimeout(tick, ms); }

  async function grabFrame() {
    // losse JPEG i.p.v. de <img>-stream: die levert geen leesbare pixels
    const r = await fetch("/api/camera_snapshot", { cache: "no-store" });
    if (!r.ok) throw new Error("camera_snapshot " + r.status);
    return createImageBitmap(await r.blob());
  }

  async function tick() {
    if (stopped) return;
    if (document.hidden) {   // pauze; visibilitychange hervat 'm
      paused = true;
      setStatus('<span class="muted">op pauze (tabblad op de achtergrond)</span>');
      return;
    }
    const t0 = performance.now();
    let bmp;
    try {
      bmp = await grabFrame();
    } catch (e) {
      missing++;
      setStatus('<span class="orange">wachten op camerabeeld…</span>');
      return schedule(missing > 5 ? 3000 : 1000);
    }
    missing = 0;
    try {
      const nw = bmp.width, nh = bmp.height;
      const preds = await model.detect(bmp);
      bmp.close();
      const persons = preds.filter(p => p.class === "person" && p.score > threshold);

      // overlay: canvas-bitmap = natuurlijke grootte, CSS schaalt mee -> geen rekenwerk
      canvas.width = nw; canvas.height = nh;
      octx.clearRect(0, 0, nw, nh);
      octx.strokeStyle = "#00eaff"; octx.fillStyle = "#00eaff";
      octx.lineWidth = Math.max(2, nw / 320);
      octx.font = `${Math.max(12, nw / 40)}px sans-serif`;
      persons.forEach(p => {
        const [x, y, w, h] = p.bbox;
        octx.strokeRect(x, y, w, h);
        octx.fillText(`Persoon ${(p.score * 100) | 0}%`, x, Math.max(12, y - 4));
      });

      const ms = Math.round(performance.now() - t0);
      const now = Date.now();
      if (persons.length) {
        lastPerson = now;
        const blocked = !timeAllowsLamp();
        if (!lampOn && !blocked) { lampOn = true; callAPI("/api/lamp/on"); }
        setStatus(`<span class="green">${persons.length} persoon${persons.length > 1 ? "en" : ""}</span> · ${ms} ms`
          + (blocked ? " · lamp in stil-venster" : ""));
      } else {
        if (lampOn && now - lastPerson > 10000) { lampOn = false; callAPI("/api/lamp/off"); }
        setStatus(`<span class="muted">geen persoon · ${ms} ms</span>`);
      }
      schedule(Math.min(3000, Math.max(500, ms * 1.5)));   // trager apparaat -> rustiger aan
    } catch (e) {
      try { bmp && bmp.close(); } catch (_) {}
      setStatus(`<span class="red">detectie-fout: ${e.message}</span>`);
      schedule(2000);
    }
  }
}

// --------------------------------------------------------------------------- //
// init: volg de Settings-toggle, ook zonder de pagina te herladen
// --------------------------------------------------------------------------- //
if (img) img.addEventListener("error", () => setStatus('<span class="red">Geen camerabeeld</span>'));

async function syncWithConfig() {
  let cfg = {};
  try { cfg = await fetch("/api/config").then(r => r.json()); } catch (_) { return; }
  const c = cfg.camera || {};
  if (c.browser_detection && !detector && Date.now() - _modelFailAt > 60000) {
    const t = Number(c.detect_threshold);
    const qf = toMin(c.lamp_quiet_from), qt = toMin(c.lamp_quiet_to);
    if (qf != null && qt != null) QUIET = { from: qf, to: qt };
    startDetection(Number.isFinite(t) && t > 0 && t < 1 ? t : 0.5);
  } else if (!c.browser_detection && detector) {
    detector.stop();
    setStatus('<span class="muted">Live (geen detectie)</span>');
  }
}

(async function init() {
  let cam = {};
  try { cam = await fetch("/api/camera_status").then(r => r.json()); } catch (_) {}
  if (cam.enabled === false) {
    if (img) img.style.display = "none";
    if (canvas) canvas.hidden = true;
    setStatus('<span class="muted">Camera staat uit (zet aan via Settings)</span>');
    return;
  }
  setStatus(cam.error ? `<span class="red">${cam.error}</span>` : '<span class="muted">Live</span>');
  await syncWithConfig();
  setInterval(syncWithConfig, 15000);
})();
