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
 *   responseProcessors: [],         // array of (text) => text functions (optional)
 * };
 */

(function () {
  // --- Config Validation ---
  function validateConfig(cfg) {
    const warnings = [];
    if (!cfg.id) warnings.push('Missing "id" — using "default". localStorage keys may collide.');
    if (!cfg.mode) warnings.push('Missing "mode" — defaulting to "simple".');
    if (cfg.mode === 'simple' && !cfg.systemPrompt) warnings.push('No "systemPrompt" set — model will have no persona.');
    if (warnings.length > 0) {
      console.warn('[chat.js] Config warnings:\n  ' + warnings.join('\n  '));
    }
  }

  const config = window.CHAT_CONFIG || {};
  validateConfig(config);

  const id = config.id || 'default';
  const mode = config.mode || 'simple';
  const stripThinking = config.stripThinking !== false;
  const defaultMaxTokens = config.maxTokens || 1500;
  const defaultTemperature = config.temperature || 0.7;
  const responseProcessors = config.responseProcessors || [];

  // =========================================================================
  // Utilities
  // =========================================================================
  function escapeHtml(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
  }

  function escapeAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function clampNumber(val, min, max, fallback) {
    const n = Number(val);
    if (isNaN(n)) return fallback;
    return Math.max(min, Math.min(max, n));
  }

  function safeSaveStorage(key, value) {
    try {
      localStorage.setItem(key, value);
    } catch (e) {
      console.warn('[chat.js] localStorage write failed:', e.message);
    }
  }

  // =========================================================================
  // Storage — reads/writes connection and settings to localStorage
  // =========================================================================
  const storage = {
    _key(suffix) { return `chat-${id}-${suffix}`; },
    getEndpoint()    { return localStorage.getItem(this._key('endpoint')) || ''; },
    getApiKey()      { return localStorage.getItem(this._key('apikey')) || ''; },
    getMaxTokens()   { return localStorage.getItem(this._key('max-tokens')) || String(defaultMaxTokens); },
    getTemperature() { return localStorage.getItem(this._key('temperature')) || String(defaultTemperature); },
    saveEndpoint(v)    { safeSaveStorage(this._key('endpoint'), v); },
    saveApiKey(v)      { safeSaveStorage(this._key('apikey'), v); },
    saveMaxTokens(v)   { safeSaveStorage(this._key('max-tokens'), v); },
    saveTemperature(v) { safeSaveStorage(this._key('temperature'), v); },
  };

  // =========================================================================
  // Connection — manages endpoint, apikey, and model detection
  // =========================================================================
  const connection = {
    endpoint: '',
    apikey: '',
    detectedModel: null,

    load() {
      this.endpoint = storage.getEndpoint();
      this.apikey = storage.getApiKey();
    },

    save() {
      storage.saveEndpoint(this.endpoint);
      storage.saveApiKey(this.apikey);
    },

    apiBase() {
      if (this.endpoint.endsWith('/v1')) return this.endpoint;
      return this.endpoint + '/v1';
    },

    modelName() {
      return this.detectedModel || '/models/weights';
    },

    async check(onStatus) {
      if (!this.endpoint) { onStatus('disconnected', 'Not connected'); return; }
      onStatus('checking', 'Connecting...');
      try {
        const res = await fetch(this.apiBase() + '/models', {
          headers: { 'Authorization': 'Bearer ' + this.apikey }
        });
        if (res.ok) {
          const data = await res.json();
          this.detectedModel = data.data?.[0]?.id || null;
          const label = mode === 'developer' ? (this.detectedModel || 'unknown') : 'Connected';
          onStatus('connected', label);
        } else {
          onStatus('disconnected', 'HTTP ' + res.status);
        }
      } catch (e) {
        onStatus('disconnected', 'Unreachable');
      }
    },
  };

  // =========================================================================
  // SSE Parser — reads a ReadableStream and yields content deltas
  // =========================================================================
  async function* parseSSEStream(reader) {
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
        if (data === '[DONE]') return;
        try {
          const json = JSON.parse(data);
          const delta = json.choices?.[0]?.delta?.content;
          if (delta) yield delta;
        } catch (e) {
          console.debug('[chat.js] SSE parse skip:', e.message);
        }
      }
    }
  }

  // =========================================================================
  // Thinking Filter — strips <think>...</think> tags from streaming text
  // =========================================================================
  function createThinkingFilter() {
    let inThinking = false;
    let display = '';

    return {
      process(delta) {
        let i = 0;
        while (i < delta.length) {
          if (!inThinking) {
            const start = delta.indexOf('<think>', i);
            if (start !== -1) {
              display += delta.slice(i, start);
              inThinking = true;
              i = start + 7;
            } else {
              display += delta.slice(i);
              i = delta.length;
            }
          } else {
            const end = delta.indexOf('</think>', i);
            if (end !== -1) {
              inThinking = false;
              i = end + 8;
            } else {
              i = delta.length;
            }
          }
        }
        return display;
      },

      finalize() {
        return display.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
      },
    };
  }

  // =========================================================================
  // API Client — sends chat completion requests
  // =========================================================================
  let currentAbortController = null;

  async function sendChatRequest(messages, maxTokens, temperature) {
    currentAbortController = new AbortController();

    const body = {
      model: connection.modelName(),
      messages: messages,
      max_tokens: maxTokens,
      temperature: temperature,
      stream: true,
    };

    if (stripThinking) {
      body.chat_template_kwargs = { enable_thinking: false };
    }

    const res = await fetch(connection.apiBase() + '/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + connection.apikey
      },
      body: JSON.stringify(body),
      signal: currentAbortController.signal,
    });

    if (!res.ok) {
      const err = await res.text();
      throw new Error('HTTP ' + res.status + ': ' + err);
    }

    return res.body.getReader();
  }

  function cancelRequest() {
    if (currentAbortController) {
      currentAbortController.abort();
      currentAbortController = null;
    }
  }

  // =========================================================================
  // DOM — layout generation and element references
  // =========================================================================
  const dom = {
    chat: null, prompt: null, send: null, clear: null,
    status: null, starters: null, overlay: null,
    endpoint: null, apikey: null, maxTokens: null, temperature: null,

    build() {
      const app = document.getElementById('chat-app') || document.body;
      app.innerHTML = '';

      if (mode === 'developer') {
        app.innerHTML += `
          <div class="settings">
            <div class="field endpoint">
              <label>Endpoint URL</label>
              <input type="text" class="js-endpoint" placeholder="https://your-pod-id-8000.proxy.runpod.net">
            </div>
            <div class="field apikey">
              <label>API Key</label>
              <input type="password" class="js-apikey" placeholder="your-api-key">
            </div>
            <div class="field">
              <label>Max Tokens</label>
              <input type="number" class="js-max-tokens" value="${defaultMaxTokens}" min="1" max="16384" style="width:80px">
            </div>
            <div class="field">
              <label>Temperature</label>
              <input type="number" class="js-temperature" value="${defaultTemperature}" min="0" max="2" step="0.1" style="width:70px">
            </div>
            <span class="status-badge disconnected">Not connected</span>
          </div>`;
      } else {
        const titleHtml = config.titleAccent
          ? `<span class="accent">${escapeHtml(config.titleAccent)}</span> ${escapeHtml(config.title || '')}`
          : escapeHtml(config.title || 'Chat');
        const subtitleHtml = config.subtitle
          ? `<div class="subtitle">${escapeHtml(config.subtitle)}</div>` : '';
        app.innerHTML += `
          <div class="header">
            <div>
              <h1>${titleHtml}</h1>
              ${subtitleHtml}
            </div>
            <span class="status-badge disconnected">Not connected</span>
          </div>`;
      }

      app.innerHTML += `
        <div class="chat"></div>
        <div class="starters"></div>
        <div class="input-area">
          <textarea class="chat-prompt" rows="1" placeholder="${escapeAttr(config.placeholder || 'Type a message...')}"></textarea>
          <button class="btn-clear">Clear</button>
          <button class="btn-send">Send</button>
        </div>`;

      if (mode === 'simple') {
        app.innerHTML += `
          <div class="setup-overlay">
            <div class="setup-box">
              <h2>Welcome!</h2>
              <p>Enter your connection details to get started. You only need to do this once.</p>
              <label>Endpoint URL</label>
              <input type="text" class="js-setup-endpoint" placeholder="https://your-pod-id-8000.proxy.runpod.net">
              <label>API Key</label>
              <input type="password" class="js-setup-key" placeholder="your-api-key">
              <button class="js-setup-connect">Connect</button>
            </div>
          </div>`;
      }
    },

    bind() {
      const q = (sel) => document.querySelector(sel);
      this.chat = q('.chat');
      this.prompt = q('.chat-prompt');
      this.send = q('.btn-send');
      this.clear = q('.btn-clear');
      this.status = q('.status-badge');
      this.starters = q('.starters');
      this.overlay = q('.setup-overlay');
      this.endpoint = q('.js-endpoint');
      this.apikey = q('.js-apikey');
      this.maxTokens = q('.js-max-tokens');
      this.temperature = q('.js-temperature');
    },

    setStatus(cls, text) {
      if (!this.status) return;
      this.status.className = 'status-badge ' + cls;
      this.status.textContent = text;
    },

    addMessage(role, content) {
      const div = document.createElement('div');
      div.className = 'message ' + role;
      if (content) div.textContent = content;
      this.chat.appendChild(div);
      this.chat.scrollTop = this.chat.scrollHeight;
      return div;
    },

    scrollToBottom() {
      this.chat.scrollTop = this.chat.scrollHeight;
    },
  };

  // =========================================================================
  // Chat Controller — orchestrates messages, sending, and UI state
  // =========================================================================
  let messages = [];
  let generating = false;

  function resetChat() {
    messages = [];
    if (config.systemPrompt) {
      messages.push({ role: 'system', content: config.systemPrompt });
    }
    dom.chat.innerHTML = '';
    if (config.welcomeMessage) {
      dom.addMessage('assistant', config.welcomeMessage);
    }
    if (dom.starters) {
      dom.starters.classList.remove('hidden');
    }
  }

  function applyProcessors(text) {
    return responseProcessors.reduce((t, fn) => fn(t), text);
  }

  async function send() {
    const text = dom.prompt.value.trim();
    if (!text) return;
    if (generating) {
      cancelRequest();
      return;
    }

    if (!connection.endpoint) {
      if (dom.overlay) dom.overlay.classList.remove('hidden');
      return;
    }

    if (dom.starters) dom.starters.classList.add('hidden');

    const userMsgIndex = messages.length;
    messages.push({ role: 'user', content: text });
    dom.addMessage('user', text);
    dom.prompt.value = '';
    dom.prompt.style.height = 'auto';

    generating = true;
    dom.send.disabled = true;

    const assistantDiv = dom.addMessage('assistant', '');
    if (stripThinking) {
      assistantDiv.innerHTML = '<span class="thinking-indicator">Thinking...</span>';
    }

    const startTime = Date.now();
    const maxTokens = clampNumber(
      dom.maxTokens ? dom.maxTokens.value : defaultMaxTokens,
      1, 16384, defaultMaxTokens
    );
    const temp = clampNumber(
      dom.temperature ? dom.temperature.value : defaultTemperature,
      0, 2, defaultTemperature
    );

    try {
      const reader = await sendChatRequest(messages, maxTokens, temp);
      const filter = stripThinking ? createThinkingFilter() : null;
      let fullContent = '';
      let thinkingCleared = !stripThinking;

      for await (const delta of parseSSEStream(reader)) {
        fullContent += delta;

        if (filter) {
          const display = filter.process(delta);
          const trimmed = display.trim();
          if (trimmed && !thinkingCleared) {
            assistantDiv.textContent = '';
            thinkingCleared = true;
          }
          if (thinkingCleared) {
            assistantDiv.textContent = trimmed;
            dom.scrollToBottom();
          }
        } else {
          assistantDiv.textContent = fullContent;
          dom.scrollToBottom();
        }
      }

      let cleanContent = filter ? filter.finalize() : fullContent;
      cleanContent = applyProcessors(cleanContent);
      assistantDiv.textContent = cleanContent;

      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      const meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = elapsed + 's';
      assistantDiv.appendChild(meta);

      messages.push({ role: 'assistant', content: cleanContent });

    } catch (e) {
      if (e.name === 'AbortError') {
        assistantDiv.className = 'message meta';
        assistantDiv.textContent = '(cancelled)';
      } else {
        assistantDiv.className = 'message error';
        assistantDiv.textContent = e.message;
      }
      // Remove the user message we added at the known index
      if (messages.length > userMsgIndex && messages[userMsgIndex]?.role === 'user') {
        messages.splice(userMsgIndex, 1);
      }
    }

    currentAbortController = null;
    generating = false;
    dom.send.disabled = false;
    dom.prompt.focus();
  }

  // =========================================================================
  // Mode Initializers — separate setup paths for developer vs simple
  // =========================================================================
  function initDeveloperMode() {
    if (dom.endpoint) {
      dom.endpoint.value = connection.endpoint;
      dom.endpoint.addEventListener('change', () => {
        const val = dom.endpoint.value.replace(/\/+$/, '');
        if (val) {
          try { new URL(val); } catch (e) {
            dom.setStatus('disconnected', 'Invalid URL');
            return;
          }
        }
        connection.endpoint = val;
        connection.save();
        connection.check((cls, text) => dom.setStatus(cls, text));
      });
    }
    if (dom.apikey) {
      dom.apikey.value = connection.apikey;
      dom.apikey.addEventListener('change', () => {
        connection.apikey = dom.apikey.value;
        connection.save();
        connection.check((cls, text) => dom.setStatus(cls, text));
      });
    }
    if (dom.maxTokens) {
      dom.maxTokens.value = storage.getMaxTokens();
      dom.maxTokens.addEventListener('change', () => {
        storage.saveMaxTokens(dom.maxTokens.value);
      });
    }
    if (dom.temperature) {
      dom.temperature.value = storage.getTemperature();
      dom.temperature.addEventListener('change', () => {
        storage.saveTemperature(dom.temperature.value);
      });
    }
    if (connection.endpoint) {
      connection.check((cls, text) => dom.setStatus(cls, text));
    }
  }

  function initSimpleMode() {
    if (connection.endpoint && connection.apikey) {
      if (dom.overlay) dom.overlay.classList.add('hidden');
      connection.check((cls, text) => dom.setStatus(cls, text));
    }

    const connectBtn = document.querySelector('.js-setup-connect');
    if (connectBtn) {
      connectBtn.addEventListener('click', () => {
        const ep = document.querySelector('.js-setup-endpoint');
        const key = document.querySelector('.js-setup-key');
        if (!ep || !key) return;
        const epVal = ep.value.replace(/\/+$/, '');
        if (!epVal || !key.value) return;
        try {
          new URL(epVal);
        } catch (e) {
          ep.style.borderColor = '#f87171';
          return;
        }
        ep.style.borderColor = '';
        connection.endpoint = epVal;
        connection.apikey = key.value;
        connection.save();
        if (dom.overlay) dom.overlay.classList.add('hidden');
        connection.check((cls, text) => dom.setStatus(cls, text));
      });
    }
  }

  // =========================================================================
  // Init — entry point
  // =========================================================================
  function init() {
    dom.build();
    dom.bind();
    connection.load();

    if (mode === 'developer') {
      initDeveloperMode();
    } else {
      initSimpleMode();
    }

    resetChat();

    if (dom.starters && config.starters && config.starters.length > 0) {
      dom.starters.innerHTML = config.starters.map(
        s => `<button class="starter-btn">${escapeHtml(s)}</button>`
      ).join('');
      dom.starters.addEventListener('click', (e) => {
        if (e.target.classList.contains('starter-btn')) {
          dom.prompt.value = e.target.textContent;
          send();
        }
      });
    }

    dom.send.addEventListener('click', send);
    dom.prompt.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
    dom.prompt.addEventListener('input', () => {
      dom.prompt.style.height = 'auto';
      dom.prompt.style.height = Math.min(dom.prompt.scrollHeight, 200) + 'px';
    });
    dom.clear.addEventListener('click', resetChat);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
