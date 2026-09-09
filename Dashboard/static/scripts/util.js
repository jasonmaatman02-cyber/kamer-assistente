// Shared helpers for every dashboard page.
(function (w) {
  // HTML-escape voor tekst die via innerHTML in de pagina komt (notities,
  // logregels, agenda-items, routine-namen — kunnen <script>/<img onerror> bevatten)
  const _E = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => _E[c]);
  }

  function jget(url, ms = 8000) {
    const c = new AbortController();
    const t = setTimeout(() => c.abort(), ms);
    return fetch(url, { signal: c.signal })
      .then(r => r.json().then(d => ({ ok: r.ok, status: r.status, d })))
      .finally(() => clearTimeout(t));
  }
  function jsend(url, method, body) {
    return fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }).then(r => r.json().then(d => ({ ok: r.ok, status: r.status, d })).catch(() => ({ ok: r.ok, status: r.status, d: {} })));
  }
  const jpost = (u, b) => jsend(u, "POST", b);
  const jput = (u, b) => jsend(u, "PUT", b);
  const jdel = (u) => jsend(u, "DELETE");

  function toast(msg, isErr) {
    let el = document.getElementById("kt-toast");
    if (!el) {
      el = document.createElement("div");
      el.id = "kt-toast";
      el.className = "toast";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.className = "toast show" + (isErr ? " err" : "");
    clearTimeout(el._t);
    el._t = setTimeout(() => (el.className = "toast" + (isErr ? " err" : "")), 3500);
  }

  w.KT = { jget, jpost, jput, jdel, toast, esc };
  w.esc = esc;   // ook los beschikbaar voor de pagina-scripts
})(window);
