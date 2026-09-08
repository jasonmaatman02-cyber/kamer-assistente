// Security camera view.
// Browser-side person detection (TensorFlow.js + coco-ssd, ~4 MB) is OFF by
// default and only loaded when Settings -> Camera -> "Personendetectie in
// browser" is enabled. Without it this page is just the MJPEG stream, which is
// far lighter on a Pi and on slow wifi.

const img = document.querySelector(".camera-feed");
const canvas = document.getElementById("canvas");
const statusEl = document.getElementById("camera-status");

function setStatus(html) { if (statusEl) statusEl.innerHTML = "Status: " + html; }

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src; s.onload = resolve; s.onerror = () => reject(new Error("kan niet laden: " + src));
    document.head.appendChild(s);
  });
}

// ---- API helper with a small cooldown so we don't spam the lamp ----
let lampBusy = false;
async function callAPI(url) {
  if (lampBusy) return;
  lampBusy = true;
  try { await fetch(url, { method: "PUT" }); }
  catch (e) { console.error("lamp API:", e); }
  setTimeout(() => (lampBusy = false), 3000);
}

function timeAllowsLamp() {
  const m = new Date().getHours() * 60 + new Date().getMinutes();
  return !(m >= 21 * 60 + 40 || m < 7 * 60); // geblokkeerd 21:40–07:00
}

async function startDetection(threshold = 0.5) {
  setStatus('<span class="orange">AI-model laden…</span>');
  try {
    await loadScript("https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@3.9.0");
    await loadScript("https://cdn.jsdelivr.net/npm/@tensorflow-models/coco-ssd");
  } catch (e) {
    setStatus('<span class="red">Model laden mislukt</span>');
    return;
  }
  const ctx = canvas.getContext("2d");
  const model = await cocoSsd.load();
  setStatus('<span class="green">Actief</span>');

  let lampOn = false, lastPerson = Date.now();
  const GONE_DELAY = 10000, DETECT_EVERY = 600;
  let last = 0;

  async function loop() {
    const now = Date.now();
    if (now - last > DETECT_EVERY && img.complete && img.naturalWidth) {
      last = now;
      canvas.width = img.clientWidth; canvas.height = img.clientHeight;
      const preds = await model.detect(img);
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const sx = canvas.width / img.naturalWidth, sy = canvas.height / img.naturalHeight;
      const persons = preds.filter(p => p.class === "person" && p.score > threshold);
      persons.forEach(p => {
        const [x, y, w, h] = p.bbox;
        ctx.strokeStyle = "#00eaff"; ctx.lineWidth = 3;
        ctx.strokeRect(x * sx, y * sy, w * sx, h * sy);
        ctx.fillStyle = "#00eaff"; ctx.font = "16px sans-serif";
        ctx.fillText(`Persoon ${(p.score * 100) | 0}%`, x * sx, y * sy - 4);
      });
      if (persons.length) {
        lastPerson = now;
        if (!lampOn && timeAllowsLamp()) { lampOn = true; callAPI("/api/lamp/on"); setStatus('<span class="green">Persoon gezien — lamp aan</span>'); }
        else if (!timeAllowsLamp()) setStatus('<span class="orange">Geblokkeerd (21:40–07:00)</span>');
      } else if (lampOn && now - lastPerson > GONE_DELAY) {
        lampOn = false; callAPI("/api/lamp/off"); setStatus('<span class="red">Niemand — lamp uit</span>');
      }
    }
    requestAnimationFrame(loop);
  }
  loop();
}

if (img) {
  img.addEventListener("error", () => {
    setStatus('<span class="red">Geen camerabeeld</span>');
  });
}

(async function init() {
  let cfg = {}, cam = {};
  try { cfg = await fetch("/api/config").then(r => r.json()); } catch (e) {}
  try { cam = await fetch("/api/camera_status").then(r => r.json()); } catch (e) {}

  if (cam && cam.enabled === false) {
    if (img) img.style.display = "none";
    if (canvas) canvas.hidden = true;
    setStatus('<span class="muted">Camera staat uit (zet aan via Settings)</span>');
    return;
  }
  if (cam && cam.error) {
    setStatus(`<span class="red">${cam.error}</span>`);
  }

  if (cfg.camera && cfg.camera.browser_detection) {
    const t = Number(cfg.camera.detect_threshold);
    startDetection(Number.isFinite(t) && t > 0 && t < 1 ? t : 0.5);
  } else {
    if (canvas) canvas.hidden = true;
    setStatus('<span class="muted">Live (geen detectie)</span>');
  }
})();
