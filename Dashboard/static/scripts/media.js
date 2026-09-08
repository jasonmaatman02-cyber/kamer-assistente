// Media Center. All Spotify calls degrade to a friendly empty state when
// Spotify is unavailable (no console spam), and poll intervals come from
// /api/config so slow-wifi setups can dial them down.

function jget(url, ms = 8000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), ms);
  return fetch(url, { signal: ctrl.signal }).then(r => r.json().then(d => ({ ok: r.ok, d })))
    .finally(() => clearTimeout(t));
}
const jpost = (url, body) => fetch(url, {
  method: "POST", headers: { "Content-Type": "application/json" },
  body: body ? JSON.stringify(body) : undefined,
}).then(r => r.json()).catch(() => ({}));
const $ = id => document.getElementById(id);

function msToTime(ms) {
  if (!ms) return "0:00";
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const radioLogos = {
  radio538: "/static/images/radio_logos/radio538.png",
  qmusic: "/static/images/radio_logos/qmusic.png",
  nporadio2: "/static/images/radio_logos/nporadio2.png",
  skyradio: "/static/images/radio_logos/skyradio.png",
  npo3fm: "/static/images/radio_logos/npo3fm.png",
  slam: "/static/images/radio_logos/slam.png",
  radio10: "/static/images/radio_logos/radio10.jpg",
  radio1: "/static/images/radio_logos/radio1.png",
  "100nl": "/static/images/radio_logos/100nl.png",
  veronica: "/static/images/radio_logos/veronica.png",
};
const FALLBACK_ART = "/static/images/radio.png";

// ---------------------------------------------------------------- playlists
async function fetchPlaylists() {
  const box = document.querySelector(".playlists-container");
  const { ok, d } = await jget("/api/playlists").catch(() => ({ ok: false, d: null }));
  if (!ok || !Array.isArray(d)) {
    box.innerHTML = `<p class="empty">${(d && d.error) ? "Spotify: " + d.error : "Spotify niet verbonden."}</p>`;
    return;
  }
  if (!d.length) { box.innerHTML = '<p class="empty">Geen playlists.</p>'; return; }
  box.innerHTML = "";
  d.forEach(p => {
    const div = document.createElement("div");
    div.className = "playlist-item";
    div.innerHTML = `
      <img src="${p.thumbnail || FALLBACK_ART}" class="playlist-thumb" onerror="this.src='${FALLBACK_ART}'">
      <div class="playlist-info"><h4>${p.naam || "?"}</h4><p>${[p.tracks_count ? p.tracks_count + " nummers" : "", p.duur].filter(Boolean).join(" • ") || " "}</p></div>
      <button class="play-btn" data-id="${p.id}"><i class="fa fa-play"></i></button>`;
    box.appendChild(div);
    div.querySelector(".play-btn").addEventListener("click", async e => {
      e.stopPropagation();
      await jpost("/api/play_playlist", { id: p.id });
      updateCurrentPlaying();
    });
    div.addEventListener("click", () => openPlaylistOverlay(p.id));
  });
}

async function openPlaylistOverlay(playlistId) {
  const overlay = $("playlist-overlay");
  overlay.classList.remove("hidden");
  overlay.dataset.playlistId = playlistId;
  const list = $("tracks-list");
  list.innerHTML = '<p class="empty">laden…</p>';
  const { ok, d } = await jget(`/api/playlist_tracks/${playlistId}`).catch(() => ({ ok: false }));
  if (!ok || !d || d.error) { list.innerHTML = `<p class="empty">${(d && d.error) || "Kan playlist niet laden"}</p>`; return; }
  $("overlay-thumb").src = d.thumbnail || FALLBACK_ART;
  $("overlay-title").textContent = d.name || "";
  $("overlay-details").textContent = `${d.tracks.length} nummers`;
  list.innerHTML = "";
  d.tracks.forEach((track, i) => {
    const div = document.createElement("div");
    div.className = "track";
    div.innerHTML = `
      <div class="track-info"><h4>${i + 1}. ${track.name}</h4><p>${track.artist}</p></div>
      <div><span>${track.duration}</span><button class="track-play" data-id="${track.id}">▶</button></div>`;
    list.appendChild(div);
    div.querySelector(".track-play").addEventListener("click", async () => {
      await jpost("/api/play_track", { track_id: track.id, playlist_id: overlay.dataset.playlistId });
      updateCurrentPlaying();
    });
  });
}
document.querySelector(".close-overlay").addEventListener("click", () =>
  $("playlist-overlay").classList.add("hidden"));

// ---------------------------------------------------------------- radio
async function loadRadioStations() {
  const box = document.querySelector(".radio-container");
  const { ok, d } = await jget("/api/radio_stations").catch(() => ({ ok: false }));
  if (!ok || !Array.isArray(d) || !d.length) {
    box.innerHTML = '<p class="empty">Radio niet beschikbaar.</p>';
    return;
  }
  box.innerHTML = "";
  d.forEach(s => {
    const div = document.createElement("div");
    div.className = "station";
    div.innerHTML = `<span>${s.name}</span>
      <div class="station-controls">
        <button class="play" data-name="${s.name}">▶</button>
        <button class="stop" data-name="${s.name}">■</button>
      </div>`;
    box.appendChild(div);
    div.querySelector(".play").addEventListener("click", async () => {
      await jpost("/api/radio_play", { station: s.name });
      updateCurrentPlaying();
    });
    div.querySelector(".stop").addEventListener("click", async () => {
      await jpost("/api/radio_stop");
      updateCurrentPlaying();
    });
  });
}

// ---------------------------------------------------------------- player
let isPlaying = false, currentType = null;
// Local playhead so the bar moves elke ~250ms i.p.v. te springen per poll.
const playhead = { pos: 0, dur: 0, playing: false, at: 0 };

async function updateCurrentPlaying() {
  const { d: data } = await jget("/api/current_playing").catch(() => ({ d: { type: "none" } }));
  const thumb = $("np-thumb"), title = $("np-title"), artist = $("np-artist");
  const progress = $("np-progress"), curT = $("np-current-time"), dur = $("np-duration");
  const btn = $("np-play-pause");
  currentType = data.type;

  if (data.type === "spotify") {
    thumb.src = data.thumbnail || FALLBACK_ART;
    title.textContent = data.name;
    artist.textContent = `${data.artist} • ${data.album}`;
    progress.max = data.duration_ms;
    isPlaying = data.is_playing;
    btn.innerHTML = isPlaying ? "⏸" : "▶";
    dur.textContent = msToTime(data.duration_ms);
    // resync de lokale playhead met de server
    playhead.pos = data.progress_ms || 0;
    playhead.dur = data.duration_ms || 0;
    playhead.playing = !!data.is_playing;
    playhead.at = Date.now();
    if (!isSeeking) { progress.value = playhead.pos; curT.textContent = msToTime(playhead.pos); }
  } else if (data.type === "radio") {
    const key = (data.station || "").toLowerCase().replace(/\s/g, "");
    thumb.src = radioLogos[key] || FALLBACK_ART;
    title.textContent = "Live Radio";
    artist.textContent = data.station || "Onbekend station";
    progress.max = 100; progress.value = 100;
    curT.textContent = data.elapsed ? msToTime(data.elapsed * 1000) : "Live";
    dur.textContent = "";
    btn.innerHTML = "⏸"; isPlaying = true;
    playhead.playing = false;
    if (volumeSlider && data.volume !== undefined) volumeSlider.value = data.volume;
  } else {
    title.textContent = "–"; artist.textContent = "–";
    thumb.src = FALLBACK_ART;
    progress.value = 0; curT.textContent = "0:00"; dur.textContent = "0:00";
    btn.innerHTML = "▶"; isPlaying = false;
    playhead.playing = false;
  }
}

// vloeiende voortgang tussen polls door
setInterval(() => {
  if (!playhead.playing || isSeeking || currentType !== "spotify") return;
  const est = Math.min(playhead.pos + (Date.now() - playhead.at), playhead.dur);
  $("np-progress").value = est;
  $("np-current-time").textContent = msToTime(est);
}, 250);

// seek
const progress = $("np-progress");
let isSeeking = false;
["mousedown", "touchstart"].forEach(ev => progress.addEventListener(ev, () => (isSeeking = true)));
["mouseup", "touchend"].forEach(ev => progress.addEventListener(ev, async () => {
  if (!isSeeking) return;
  await jpost("/api/seek", { position_ms: parseInt(progress.value) });
  isSeeking = false;
}));

$("np-play-pause").addEventListener("click", async () => {
  const url = currentType === "radio"
    ? (isPlaying ? "/api/radio_pause" : "/api/radio_resume")
    : (isPlaying ? "/api/spotify_pause" : "/api/spotify_resume");
  await jpost(url); updateCurrentPlaying();
});
$("next-btn").addEventListener("click", async () => { await jpost("/api/spotify_next"); updateCurrentPlaying(); });
$("prev-btn").addEventListener("click", async () => { await jpost("/api/spotify_previous"); updateCurrentPlaying(); });

// volume
const volumeSlider = $("volume-slider");
let isDragging = false, currentVolume = 50;
volumeSlider.addEventListener("input", e => { currentVolume = parseInt(e.target.value); isDragging = true; });
["change", "mouseup", "touchend"].forEach(ev => volumeSlider.addEventListener(ev, async () => {
  if (!isDragging) return;
  await jpost("/api/set_volume", { volume: currentVolume });
  isDragging = false;
}));

// ---------------------------------------------------------------- spotify devices
let selectedDeviceId = null;
let deviceFails = 0;
async function loadSpotifyDevices() {
  const box = $("devices-menu");
  const connectBtn = $("connect-device-btn");
  const { ok, d } = await jget("/api/devices").catch(() => ({ ok: false }));
  if (!ok || !d || !d.success) {
    deviceFails++;
    box.innerHTML = '<p class="empty">Geen Spotify-verbinding.</p>';
    connectBtn.disabled = true;
    return;
  }
  deviceFails = 0;
  box.innerHTML = ""; connectBtn.disabled = true; selectedDeviceId = null;
  if (!d.devices.length) { box.innerHTML = '<p class="empty">Geen apparaten gevonden.</p>'; return; }
  d.devices.forEach(dev => {
    const div = document.createElement("div");
    div.className = "device-item";
    div.textContent = `${dev.name} (${dev.type})${dev.active ? " ✅" : ""}`;
    div.addEventListener("click", () => {
      box.querySelectorAll(".device-item").forEach(x => x.classList.remove("selected"));
      div.classList.add("selected");
      selectedDeviceId = dev.id;
      connectBtn.disabled = false;
    });
    box.appendChild(div);
  });
}
$("connect-device-btn").addEventListener("click", async () => {
  if (!selectedDeviceId) return;
  const r = await jpost("/api/set_device", { device_id: selectedDeviceId });
  if (r.success) loadSpotifyDevices();
});

// ---------------------------------------------------------------- search
const searchInput = $("spotify-search");
const results = $("search-results");
let searchTimer = null;
searchInput.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = searchInput.value.trim();
  if (!q) { results.innerHTML = ""; return; }
  searchTimer = setTimeout(async () => {
    const { d } = await jget(`/api/search_spotify?query=${encodeURIComponent(q)}`).catch(() => ({ d: {} }));
    const tracks = (d && d.tracks) || [];
    if (!tracks.length) { results.innerHTML = `<p class="empty">${(d && d.error) ? d.error : "Geen resultaten."}</p>`; return; }
    results.innerHTML = "";
    tracks.forEach(track => {
      const div = document.createElement("div");
      div.className = "search-result-item";
      div.innerHTML = `
        <img src="${track.thumbnail || FALLBACK_ART}" onerror="this.src='${FALLBACK_ART}'">
        <div class="search-result-info"><h4>${track.name}</h4><p>${track.artist}</p></div>
        <button class="play-track-btn" data-id="${track.id}">▶</button>`;
      div.querySelector(".play-track-btn").addEventListener("click", async e => {
        e.stopPropagation();
        await jpost("/api/play_track", { track_id: track.id });
        updateCurrentPlaying();
      });
      results.appendChild(div);
    });
  }, 350);
});

// ---------------------------------------------------------------- init
(async function init() {
  let cfg = { poll: {} };
  try { cfg = (await jget("/api/config")).d || cfg; } catch (e) {}
  const p = cfg.poll || {};
  const npMs = Math.max(p.poll_now_playing_ms || 4000, 2000);
  const devMs = Math.max(p.poll_devices_ms || 15000, 5000);

  updateCurrentPlaying();
  fetchPlaylists();
  loadRadioStations();
  loadSpotifyDevices();

  setInterval(updateCurrentPlaying, npMs);
  setInterval(() => { if (deviceFails < 3) loadSpotifyDevices(); }, devMs);
})();
