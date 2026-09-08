const sendBtn = document.getElementById('sendBtn');
const userInput = document.getElementById('userInput');
const chatWindow = document.getElementById('chatWindow');
let busy = false;

// Focus terug naar het invoerveld, maar niet als de gebruiker tekst selecteert
// of op een link/knop klikt.
window.addEventListener('load', () => userInput.focus());
document.addEventListener('click', (e) => {
  if (busy) return;
  if (window.getSelection().toString()) return;
  if (e.target.closest('a, button, input, textarea, .chat-bubble')) return;
  userInput.focus();
});

userInput.addEventListener('input', () => {
  userInput.style.height = 'auto';
  userInput.style.height = Math.min(userInput.scrollHeight, 160) + 'px';
});

userInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});

function appendMessage(sender, text) {
  const bubble = document.createElement('div');
  bubble.classList.add('chat-bubble', sender);
  bubble.textContent = text;
  chatWindow.appendChild(bubble);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return bubble;
}

function showThinking() {
  const bubble = document.createElement('div');
  bubble.classList.add('chat-bubble', 'ai');
  bubble.innerHTML = '<div class="typing-dots"><span></span><span></span><span></span></div>';
  chatWindow.appendChild(bubble);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return bubble;
}

function setBusy(state) {
  busy = state;
  sendBtn.disabled = state;
  userInput.disabled = state;
  if (!state) userInput.focus();
}

async function send() {
  if (busy) return;
  const text = userInput.value.trim();
  if (!text) return;

  appendMessage('user', text);
  userInput.value = '';
  userInput.style.height = 'auto';
  setBusy(true);
  const thinking = showThinking();

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 120000);
  try {
    const res = await fetch('/api/send_message', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text }),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`server ${res.status}`);
    const data = await res.json();
    thinking.remove();
    appendMessage('ai', data.reply || '(geen antwoord)');
  } catch (err) {
    thinking.remove();
    const msg = err.name === 'AbortError'
      ? 'Time-out — de AI deed er te lang over. Probeer een kortere vraag of een kleiner model.'
      : `Fout bij communicatie met de server: ${err.message}`;
    appendMessage('ai', 'error: ' + msg);
  } finally {
    clearTimeout(timer);
    setBusy(false);
  }
}

sendBtn.addEventListener('click', send);
