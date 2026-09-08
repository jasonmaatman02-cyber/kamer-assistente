const j = (u, o = {}, ms = 8000) => {
  const c = new AbortController();
  const t = setTimeout(() => c.abort(), ms);
  return fetch(u, { ...o, signal: c.signal }).then(r => r.json()).finally(() => clearTimeout(t));
};

async function loadWeather() {
  try {
    const d = await j("/api/weather");
    if (!d || d.error) { document.getElementById("w-city").textContent = "niet beschikbaar"; return; }
    document.getElementById("w-city").textContent = d.city;
    document.getElementById("w-temp").textContent = `${d.temp} °C`;
    document.getElementById("w-condition").textContent = d.condition;
    document.getElementById("w-wind").textContent = `${d.wind} km/h`;
    document.getElementById("w-humidity").textContent = `${d.humidity}%`;
    document.getElementById("w-updated").textContent = "Bijgewerkt: " + new Date().toLocaleTimeString("nl-NL");
  } catch (e) { console.error(e); }
}

async function loadCity() {
  try {
    const s = await j("/api/settings");
    document.getElementById("city").value = s.settings?.weather?.city || "";
  } catch (e) {}
}

document.getElementById("save-city").addEventListener("click", async () => {
  const city = document.getElementById("city").value.trim();
  const msg = document.getElementById("city-msg");
  if (!city) return;
  msg.textContent = "Opslaan…";
  await fetch("/api/settings", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ weather: { city } }),
  });
  msg.textContent = "Opgeslagen ✓";
  loadWeather();
  setTimeout(() => (msg.textContent = ""), 3000);
});

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
  thermoTimer = setTimeout(() => j("/api/thermostat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ target: +thermo.value }),
  }).catch(() => {}), 500);
});

initThermo();
loadCity();
loadWeather();
setInterval(loadWeather, 600000);
