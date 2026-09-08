// AI Assistent Kamer Dashboard - JavaScript

class Dashboard {
    constructor() {
        this.currentPanel = 'chat';
        this.radioPlaying = false;
        this.currentStation = null;
        
        this.initEventListeners();
        this.updateTime();
        this.simulateWifi();
        this.initChat();
    }

    initEventListeners() {
        // Navigation
        document.querySelectorAll('.nav-item').forEach(item => {
            item.addEventListener('click', (e) => {
                const panel = e.target.dataset.panel;
                this.switchPanel(panel);
            });
        });

        // Chat
        document.getElementById('send-btn').addEventListener('click', () => this.sendMessage());
        document.getElementById('chat-input').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this.sendMessage();
        });

        // Devices
        document.querySelectorAll('.toggle-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.toggleLamp(e.target));
        });

        document.querySelectorAll('.color-picker, .brightness-slider').forEach(control => {
            control.addEventListener('input', (e) => this.updateLamp(e.target));
        });

        // Radio
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.addEventListener('click', (e) => this.selectStation(e.target));
        });

        document.getElementById('radio-toggle').addEventListener('click', () => this.toggleRadio());
        document.getElementById('radio-volume').addEventListener('input', (e) => this.setVolume(e.target.value));

        // Music
        document.getElementById('music-search-btn').addEventListener('click', () => this.searchMusic());
        document.getElementById('music-pause-btn').addEventListener('click', () => this.pauseMusic());
        document.getElementById('music-resume-btn').addEventListener('click', () => this.resumeMusic());
        document.getElementById('music-stop-btn').addEventListener('click', () => this.stopMusic());
    }

    switchPanel(panelName) {
        // Update navigation
        document.querySelectorAll('.nav-item').forEach(item => {
            item.classList.remove('active');
        });
        document.querySelector(`[data-panel="${panelName}"]`).classList.add('active');

        // Update panels
        document.querySelectorAll('.panel').forEach(panel => {
            panel.classList.remove('active');
        });
        document.querySelector(`.${panelName}-panel`).classList.add('active');

        this.currentPanel = panelName;

        // Load panel specific data
        if (panelName === 'logs') {
            this.loadLogs();
        }
    }

    // Chat functions
    initChat() {
        this.addMessage('Hallo! Ik ben je AI-assistent. Hoe kan ik je helpen met je kamer?', 'ai');
    }

    addMessage(text, sender) {
        const messagesContainer = document.getElementById('chat-messages');
        const messageDiv = document.createElement('div');
        messageDiv.classList.add('message', sender);
        messageDiv.textContent = text;
        messagesContainer.appendChild(messageDiv);
        messagesContainer.scrollTop = messagesContainer.scrollHeight;
    }

    async sendMessage() {
        const input = document.getElementById('chat-input');
        const message = input.value.trim();
        
        if (!message) return;

        this.addMessage(message, 'user');
        input.value = '';

        // Simulate AI thinking
        this.addMessage('AI is aan het nadenken...', 'ai');

        try {
            const response = await fetch('/api/gpt_handler', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ input: message })
            });
            const data = await response.json();
            
            // Replace last message
            const messages = document.querySelectorAll('.message.ai');
            messages[messages.length - 1].textContent = data.response;
        } catch (error) {
            const messages = document.querySelectorAll('.message.ai');
            messages[messages.length - 1].textContent = '❌ Fout bij ophalen AI response.';
            console.error(error);
        }
    }

    // Device functions
    toggleLamp(button) {
        const isOff = button.classList.contains('off');
        button.classList.toggle('off');
        button.textContent = isOff ? 'Aan' : 'Uit';
        
        const lampId = button.dataset.lamp;
        const card = button.closest('.device-card');
        const color = card.querySelector('.color-picker').value;
        const brightness = card.querySelector('.brightness-slider').value;
        
        this.updateLampAPI(lampId, !isOff, color, brightness);
    }

    updateLamp(control) {
        const card = control.closest('.device-card');
        const button = card.querySelector('.toggle-btn');
        const lampId = button.dataset.lamp;
        const color = card.querySelector('.color-picker').value;
        const brightness = card.querySelector('.brightness-slider').value;
        const isOn = !button.classList.contains('off');
        
        this.updateLampAPI(lampId, isOn, color, brightness);
    }

    async updateLampAPI(lampId, status, color, brightness) {
        try {
            const response = await fetch('/api/update_lamp', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ lamp: lampId, status, color, brightness })
            });
            const data = await response.json();
            console.log('Lamp response:', data);
        } catch (error) {
            console.error('Lamp update failed:', error);
        }
    }

    // Radio functions
    selectStation(button) {
        // Remove active from all
        document.querySelectorAll('.preset-btn').forEach(btn => {
            btn.classList.remove('active');
        });
        
        // Add active to clicked
        button.classList.add('active');
        
        this.currentStation = button.dataset.station;
        this.updateRadioStatus(`Overschakelen naar ${this.currentStation}...`);
        
        this.setRadioStation(this.currentStation);
    }

    async setRadioStation(station) {
        try {
            await fetch('/api/radio_station', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ station })
            });
            
            this.radioPlaying = true;
            this.updateRadioToggle();
            this.updateRadioStatus(`Nu: ${station}`);
        } catch (error) {
            this.updateRadioStatus('❌ Fout bij instellen station');
            console.error(error);
        }
    }

    async toggleRadio() {
        if (!this.currentStation) {
            this.updateRadioStatus('❗ Kies eerst een station.');
            return;
        }

        this.radioPlaying = !this.radioPlaying;
        this.updateRadioToggle();
        
        try {
            await fetch('/api/radio_toggle', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    playing: this.radioPlaying, 
                    station: this.currentStation 
                })
            });
        } catch (error) {
            console.error('Radio toggle failed:', error);
        }
    }

    updateRadioToggle() {
        const toggleBtn = document.getElementById('radio-toggle');
        toggleBtn.textContent = this.radioPlaying ? '⏸️' : '▶️';
        this.updateRadioStatus(this.radioPlaying ? `Spelen: ${this.currentStation}` : 'Gestopt.');
    }

    async setVolume(volume) {
        this.updateRadioStatus(`Volume: ${volume}%`);
        
        try {
            await fetch('/api/radio_volume', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ volume })
            });
        } catch (error) {
            console.error('Volume update failed:', error);
        }
    }

    updateRadioStatus(message) {
        document.getElementById('radio-status').textContent = message;
    }

    // Music functions
    async searchMusic() {
        const searchInput = document.getElementById('music-search');
        const query = searchInput.value.trim();
        
        if (!query) return;

        this.updateMusicStatus(`🔍 Zoeken naar: ${query}`);

        try {
            const response = await fetch('/api/speel_muziek', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ zoekterm: query })
            });
            const data = await response.json();

            if (data.success) {
                this.updateMusicStatus(`▶️ Afspelen: ${query}`);
            } else {
                this.updateMusicStatus(`❌ ${data.message}`);
            }
        } catch (error) {
            this.updateMusicStatus('❌ Fout bij het zoeken');
            console.error(error);
        }
    }

    async pauseMusic() {
        try {
            await fetch('/api/muziek_pauze', { method: 'POST' });
            this.updateMusicStatus('⏸️ Muziek gepauzeerd');
        } catch (error) {
            console.error('Pause failed:', error);
        }
    }

    async resumeMusic() {
        try {
            await fetch('/api/muziek_resume', { method: 'POST' });
            this.updateMusicStatus('▶️ Muziek hervat');
        } catch (error) {
            console.error('Resume failed:', error);
        }
    }

    async stopMusic() {
        try {
            await fetch('/api/muziek_stop', { method: 'POST' });
            this.updateMusicStatus('🛑 Muziek gestopt');
        } catch (error) {
            console.error('Stop failed:', error);
        }
    }

    updateMusicStatus(message) {
        document.getElementById('music-status').textContent = message;
    }

    // Logs functions
    async loadLogs() {
        const container = document.getElementById('logs-container');
        container.innerHTML = '<p>Bezig met laden...</p>';

        try {
            const response = await fetch('/api/get_logs');
            const logs = await response.json();
            
            container.innerHTML = '';
            
            for (const [date, content] of Object.entries(logs)) {
                const details = document.createElement('details');
                details.className = 'log-entry';
                details.innerHTML = `
                    <summary>${date}</summary>
                    <pre>${content}</pre>
                `;
                container.appendChild(details);
            }
        } catch (error) {
            container.innerHTML = '<p class="status-text">❌ Fout bij laden van logs.</p>';
            console.error(error);
        }
    }

    // Status functions
    updateTime() {
        const timeDisplay = document.querySelector('.time-display');
        const now = new Date();
        timeDisplay.textContent = `Tijd: ${now.toLocaleTimeString('nl-NL')}`;
    }

    simulateWifi() {
        const wifiText = document.querySelector('.wifi-text');
        const statuses = ['Goed', 'Matig', 'Geen verbinding'];
        const randomStatus = statuses[Math.floor(Math.random() * statuses.length)];
        wifiText.textContent = `Verbinding: ${randomStatus}`;
    }
}

// Initialize dashboard when page loads
document.addEventListener('DOMContentLoaded', () => {
    const dashboard = new Dashboard();
    
    // Update time every second
    setInterval(() => dashboard.updateTime(), 1000);
    
    // Simulate wifi status changes
    setInterval(() => dashboard.simulateWifi(), 15000);
});
