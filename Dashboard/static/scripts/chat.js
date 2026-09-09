const sendBtn = document.getElementById('sendBtn');
const userInput = document.getElementById('userInput');
const chatWindow = document.getElementById('chatWindow');
const HIST_KEY = 'kamer_chat';
let busy = false;

// stabiele sessie-id per browser, zodat de servergeschiedenis van deze tab
// niet met de spraakassistent of een andere tab mengt
const CHAT_SID = (() => {
  try {
    let s = localStorage.getItem('kamer_chat_sid');
    if (!s) { s = Date.now().toString(36) + Math.random().toString(36).slice(2, 8); localStorage.setItem('kamer_chat_sid', s); }
    return s;
  } catch (e) { return 'web'; }
})();

// ---- history (localStorage, per-browser) ----
function loadHistory() {
  let hist = [];
  try { hist = JSON.parse(localStorage.getItem(HIST_KEY) || '[]'); } catch (e) {}
  if (hist.length) {
    chatWindow.innerHTML = '';
    hist.forEach(m => appendMessage(m.role, m.text, false));
  }
}
function saveHistory() {
  const msgs = [...chatWindow.querySelectorAll('.chat-bubble')]
    .filter(b => !b.querySelector('.typing-dots'))
    .map(b => ({ role: b.classList.contains('user') ? 'user' : 'ai', text: b.textContent }));
  try { localStorage.setItem(HIST_KEY, JSON.stringify(msgs.slice(-40))); } catch (e) {}
}

window.addEventListener('load', () => { loadHistory(); userInput.focus(); });
document.addEventListener('click', (e) => {
  if (busy || window.getSelection().toString()) return;
  if (e.target.closest('a, button, input, textarea, .chat-bubble')) return;
  userInput.focus();
});
userInput.addEventListener('input', () => {
  userInput.style.height = 'auto';
  userInput.style.height = Math.min(userInput.scrollHeight, 160) + 'px';
});
userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
});

function appendMessage(sender, text, persist = true) {
  const bubble = document.createElement('div');
  bubble.classList.add('chat-bubble', sender === 'user' ? 'user' : 'ai');
  bubble.textContent = text;
  chatWindow.appendChild(bubble);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  if (persist) saveHistory();
  return bubble;
}
function showThinking() {
  const b = document.createElement('div');
  b.classList.add('chat-bubble', 'ai');
  b.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  chatWindow.appendChild(b);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return b;
}
function setBusy(s) {
  busy = s; sendBtn.disabled = s; userInput.disabled = s;
  if (!s) userInput.focus();
}

async function send() {
  if (busy) return;
  const text = userInput.value.trim();
  if (!text) return;
  appendMessage('user', text);
  userInput.value = ''; userInput.style.height = 'auto';
  setBusy(true);

  const bubble = showThinking();
  let got = '';
  const es = new EventSource('/api/chat_stream?message=' + encodeURIComponent(text) + '&sid=' + encodeURIComponent(CHAT_SID));
  const timer = setTimeout(() => { es.close(); finish('Time-out — probeer een kortere vraag of een kleiner model.'); }, 120000);

  function finish(errText) {
    clearTimeout(timer);
    if (errText) bubble.textContent = 'error: ' + errText;
    setBusy(false);
    saveHistory();
  }

  es.onmessage = (e) => {
    let data;
    try { data = JSON.parse(e.data); } catch (_) { return; }
    if (data.done) { es.close(); finish(); return; }
    if (data.t) {
      if (!got) bubble.textContent = '';
      got += data.t;
      bubble.textContent = got;
      chatWindow.scrollTop = chatWindow.scrollHeight;
    }
  };
  es.onerror = () => { es.close(); finish(got ? null : 'Verbinding met de server verbroken.'); };
}

sendBtn.addEventListener('click', send);

// wissen — ook de serverkant van deze sessie leegmaken
const clearBtn = document.getElementById('clearChat');
if (clearBtn) clearBtn.addEventListener('click', () => {
  localStorage.removeItem(HIST_KEY);
  chatWindow.innerHTML = '<div class="chat-bubble ai">Hey Jason! Hoe kan ik je helpen?</div>';
  fetch('/api/chat/reset', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sid: CHAT_SID }),
  }).catch(() => {});
});
