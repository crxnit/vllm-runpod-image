/**
 * shared/chat.js — Shared chat engine for all UIs.
 *
 * Usage: define a CHAT_CONFIG object before loading this script.
 * The script auto-generates all required DOM elements.
 *
 * CHAT_CONFIG = {
 *   id: 'college-advisor',          // unique ID for localStorage keys
 *   systemPrompt: '...',            // system prompt string
 *   welcomeMessage: '...',          // initial assistant message (optional)
 *   starters: ['...', '...'],       // conversation starter buttons (optional)
 *   placeholder: 'Ask a question',  // textarea placeholder (optional)
 *   maxTokens: 1500,                // default max tokens
 *   temperature: 0.6,               // default temperature
 *   stripThinking: true,            // strip <think> tags from Qwen3 (default: true)
 *   mode: 'simple' | 'developer',  // 'simple' = setup overlay, 'developer' = settings bar
 *   title: 'Chat',                  // header title (optional, simple mode only)
 *   titleAccent: 'College',         // accented portion of title (optional)
 *   subtitle: '...',                // subtitle text (optional)
 * };
 */

(function () {
  const config = window.CHAT_CONFIG || {};
  const id = config.id || 'default';
  const mode = config.mode || 'simple';
  const stripThinking = config.stripThinking !== false;
  const defaultMaxTokens = config.maxTokens || 1500;
  const defaultTemperature = config.temperature || 0.7;

  // --- DOM Generation ---
  function buildLayout() {
    const container = document.getElementById('chat-app');
    if (!container) {
      document.body.innerHTML = '<div id="chat-app"></div>';
    }
    const app = document.getElementById('chat-app') || document.body;
    app.innerHTML = '';

    if (mode === 'developer') {
      app.innerHTML += `
        <div class="settings">
          <div class="field endpoint">
            <label>Endpoint URL</label>
            <input type="text" id="endpoint" placeholder="https://your-pod-id-8000.proxy.runpod.net">
          </div>
          <div class="field apikey">
            <label>API Key</label>
            <input type="password" id="apikey" placeholder="your-api-key">
          </div>
          <div class="field">
            <label>Max Tokens</label>
            <input type="number" id="max-tokens" value="${defaultMaxTokens}" min="1" max="16384" style="width:80px">
          </div>
          <div class="field">
            <label>Temperature</label>
            <input type="number" id="temperature" value="${defaultTemperature}" min="0" max="2" step="0.1" style="width:70px">
          </div>
          <span id="status" class="disconnected">Not connected</span>
        </div>`;
    } else {
      const titleHtml = config.titleAccent
        ? `<span class="accent">${config.titleAccent}</span> ${config.title || ''}`
        : (config.title || 'Chat');
      const subtitleHtml = config.subtitle
        ? `<div class="subtitle">${config.subtitle}</div>`
        : '';
      app.innerHTML += `
        <div class="header">
          <div>
            <h1>${titleHtml}</h1>
            ${subtitleHtml}
          </div>
          <span id="status" class="disconnected">Not connected</span>
        </div>`;
    }

    app.innerHTML += `
      <div class="chat" id="chat"></div>
      <div class="starters" id="starters"></div>
      <div class="input-area">
        <textarea id="prompt" rows="1" placeholder="${config.placeholder || 'Type a message...'}"></textarea>
        <button id="clear">Clear</button>
        <button id="send">Send</button>
      </div>`;

    if (mode === 'simple') {
      app.innerHTML += `
        <div class="setup-overlay" id="setup-overlay">
          <div class="setup-box">
            <h2>Welcome!</h2>
            <p>Enter your connection details to get started. You only need to do this once.</p>
            <label>Endpoint URL</label>
            <input type="text" id="setup-endpoint" placeholder="https://your-pod-id-8000.proxy.runpod.net">
            <label>API Key</label>
            <input type="password" id="setup-key" placeholder="your-api-key">
            <button id="setup-connect-btn">Connect</button>
          </div>
        </div>`;
    }
  }

  // --- Storage helpers ---
  function storageKey(suffix) {
    return `chat-${id}-${suffix}`;
  }

  // State
  let messages = [];
  let generating = false;
  let endpoint = '';
  let apikey = '';
  let detectedModel = null;

  // DOM references (set after buildLayout)
  let chatEl, promptEl, sendBtn, clearBtn, statusEl, startersEl, setupOverlay;
  let endpointEl, apikeyEl, maxTokensEl, temperatureEl;

  function bindElements() {
    chatEl = document.getElementById('chat');
    promptEl = document.getElementById('prompt');
    sendBtn = document.getElementById('send');
    clearBtn = document.getElementById('clear');
    statusEl = document.getElementById('status');
    startersEl = document.getElementById('starters');
    setupOverlay = document.getElementById('setup-overlay');
    endpointEl = document.getElementById('endpoint');
    apikeyEl = document.getElementById('apikey');
    maxTokensEl = document.getElementById('max-tokens');
    temperatureEl = document.getElementById('temperature');
  }

  function loadConnection() {
    endpoint = localStorage.getItem(storageKey('endpoint')) || '';
    apikey = localStorage.getItem(storageKey('apikey')) || '';
  }

  function saveConnection() {
    localStorage.setItem(storageKey('endpoint'), endpoint);
    localStorage.setItem(storageKey('apikey'), apikey);
  }

  function apiBase() {
    if (endpoint.endsWith('/v1')) return endpoint;
    return endpoint + '/v1';
  }

  // --- Connection ---
  async function checkConnection() {
    if (!endpoint) { setStatus('disconnected', 'Not connected'); return; }
    setStatus('checking', 'Connecting...');
    try {
      const res = await fetch(apiBase() + '/models', {
        headers: { 'Authorization': 'Bearer ' + apikey }
      });
      if (res.ok) {
        const data = await res.json();
        detectedModel = data.data?.[0]?.id || null;
        setStatus('connected', mode === 'developer' ? (detectedModel || 'unknown') : 'Connected');
      } else {
        setStatus('disconnected', 'HTTP ' + res.status);
      }
    } catch (e) {
      setStatus('disconnected', 'Unreachable');
    }
  }

  function setStatus(cls, text) {
    if (!statusEl) return;
    statusEl.className = cls;
    statusEl.textContent = text;
  }

  // --- Messages ---
  function addMessage(role, content) {
    const div = document.createElement('div');
    div.className = 'message ' + role;
    if (content) div.textContent = content;
    chatEl.appendChild(div);
    chatEl.scrollTop = chatEl.scrollHeight;
    return div;
  }

  function resetChat() {
    messages = [];
    if (config.systemPrompt) {
      messages.push({ role: 'system', content: config.systemPrompt });
    }
    chatEl.innerHTML = '';
    if (config.welcomeMessage) {
      addMessage('assistant', config.welcomeMessage);
    }
    if (startersEl) {
      startersEl.classList.remove('hidden');
    }
  }

  // --- Streaming ---
  async function send() {
    const text = promptEl.value.trim();
    if (!text || generating) return;

    if (!endpoint) {
      if (setupOverlay) setupOverlay.classList.remove('hidden');
      return;
    }

    if (startersEl) startersEl.classList.add('hidden');

    messages.push({ role: 'user', content: text });
    addMessage('user', text);
    promptEl.value = '';
    promptEl.style.height = 'auto';

    generating = true;
    sendBtn.disabled = true;

    const assistantDiv = addMessage('assistant', '');
    if (stripThinking) {
      assistantDiv.innerHTML = '<span class="thinking-indicator">Thinking...</span>';
    }

    let fullContent = '';
    let displayContent = '';
    let inThinking = false;
    let thinkingCleared = !stripThinking;
    const startTime = Date.now();

    const maxTokens = maxTokensEl ? parseInt(maxTokensEl.value) : defaultMaxTokens;
    const temp = temperatureEl ? parseFloat(temperatureEl.value) : defaultTemperature;

    try {
      const body = {
        model: detectedModel || '/models/weights',
        messages: messages,
        max_tokens: maxTokens,
        temperature: temp,
        stream: true,
      };

      if (stripThinking) {
        body.chat_template_kwargs = { enable_thinking: false };
      }

      const res = await fetch(apiBase() + '/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': 'Bearer ' + apikey
        },
        body: JSON.stringify(body)
      });

      if (!res.ok) {
        const err = await res.text();
        throw new Error('HTTP ' + res.status + ': ' + err);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop();

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const data = line.slice(6);
          if (data === '[DONE]') break;
          try {
            const json = JSON.parse(data);
            const delta = json.choices?.[0]?.delta?.content;
            if (delta) {
              fullContent += delta;

              if (stripThinking) {
                let i = 0;
                while (i < delta.length) {
                  if (!inThinking) {
                    const thinkStart = delta.indexOf('<think>', i);
                    if (thinkStart !== -1) {
                      displayContent += delta.slice(i, thinkStart);
                      inThinking = true;
                      i = thinkStart + 7;
                    } else {
                      displayContent += delta.slice(i);
                      i = delta.length;
                    }
                  } else {
                    const thinkEnd = delta.indexOf('</think>', i);
                    if (thinkEnd !== -1) {
                      inThinking = false;
                      i = thinkEnd + 8;
                    } else {
                      i = delta.length;
                    }
                  }
                }

                const trimmed = displayContent.trim();
                if (trimmed && !thinkingCleared) {
                  assistantDiv.textContent = '';
                  thinkingCleared = true;
                }
                if (thinkingCleared) {
                  assistantDiv.textContent = trimmed;
                  chatEl.scrollTop = chatEl.scrollHeight;
                }
              } else {
                assistantDiv.textContent = fullContent;
                chatEl.scrollTop = chatEl.scrollHeight;
              }
            }
          } catch (e) { /* SSE parse error — skip malformed chunk */ }
        }
      }

      const cleanContent = stripThinking
        ? displayContent.replace(/<think>[\s\S]*?<\/think>/g, '').trim()
        : fullContent;
      assistantDiv.textContent = cleanContent;

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = elapsed + 's';
      assistantDiv.appendChild(meta);

      messages.push({ role: 'assistant', content: cleanContent });

    } catch (e) {
      assistantDiv.className = 'message error';
      assistantDiv.textContent = e.message;
      messages.pop();
    }

    generating = false;
    sendBtn.disabled = false;
    promptEl.focus();
  }

  // --- Initialization ---
  function init() {
    buildLayout();
    bindElements();
    loadConnection();

    if (mode === 'developer') {
      if (endpointEl) {
        endpointEl.value = endpoint;
        endpointEl.addEventListener('change', () => {
          endpoint = endpointEl.value.replace(/\/+$/, '');
          saveConnection();
          checkConnection();
        });
      }
      if (apikeyEl) {
        apikeyEl.value = apikey;
        apikeyEl.addEventListener('change', () => {
          apikey = apikeyEl.value;
          saveConnection();
          checkConnection();
        });
      }
      if (maxTokensEl) {
        maxTokensEl.value = localStorage.getItem(storageKey('max-tokens')) || defaultMaxTokens;
        maxTokensEl.addEventListener('change', () => {
          localStorage.setItem(storageKey('max-tokens'), maxTokensEl.value);
        });
      }
      if (temperatureEl) {
        temperatureEl.value = localStorage.getItem(storageKey('temperature')) || defaultTemperature;
        temperatureEl.addEventListener('change', () => {
          localStorage.setItem(storageKey('temperature'), temperatureEl.value);
        });
      }
      if (endpoint) checkConnection();
    } else {
      if (endpoint && apikey) {
        if (setupOverlay) setupOverlay.classList.add('hidden');
        checkConnection();
      }

      const connectBtn = document.getElementById('setup-connect-btn');
      if (connectBtn) {
        connectBtn.addEventListener('click', () => {
          const ep = document.getElementById('setup-endpoint');
          const key = document.getElementById('setup-key');
          if (!ep || !key) return;
          endpoint = ep.value.replace(/\/+$/, '');
          apikey = key.value;
          if (!endpoint || !apikey) return;
          saveConnection();
          if (setupOverlay) setupOverlay.classList.add('hidden');
          checkConnection();
        });
      }
    }

    resetChat();

    // Render starters with event delegation
    if (startersEl && config.starters && config.starters.length > 0) {
      startersEl.innerHTML = config.starters.map(
        s => `<button class="starter-btn">${s}</button>`
      ).join('');
      startersEl.addEventListener('click', (e) => {
        if (e.target.classList.contains('starter-btn')) {
          promptEl.value = e.target.textContent;
          send();
        }
      });
    }

    sendBtn.addEventListener('click', send);
    promptEl.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
    promptEl.addEventListener('input', () => {
      promptEl.style.height = 'auto';
      promptEl.style.height = Math.min(promptEl.scrollHeight, 200) + 'px';
    });
    clearBtn.addEventListener('click', resetChat);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
