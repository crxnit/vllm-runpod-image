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
 *   endpoint: '',                   // pre-configured endpoint URL (optional)
 *   apikey: '',                     // pre-configured API key (optional)
 *   responseProcessors: [],         // array of (text) => text functions (optional)
 * };
 */

(function () {
  var escapeHtml = window.escapeHtml;

  // --- Config Validation ---
  function validateConfig(cfg) {
    var warnings = [];
    if (!cfg.id) warnings.push('Missing "id" — using "default". localStorage keys may collide.');
    if (!cfg.mode) warnings.push('Missing "mode" — defaulting to "simple".');
    if (cfg.mode === 'simple' && !cfg.systemPrompt) warnings.push('No "systemPrompt" set — model will have no persona.');
    if (warnings.length > 0) {
      console.warn('[chat.js] Config warnings:\n  ' + warnings.join('\n  '));
    }
  }

  var config = window.CHAT_CONFIG || {};
  validateConfig(config);

  var id = config.id || 'default';
  var mode = config.mode || 'simple';
  var stripThinking = config.stripThinking !== false;
  var defaultMaxTokens = config.maxTokens || 1500;
  var defaultTemperature = config.temperature || 0.7;
  var responseProcessors = config.responseProcessors || [];
  var preconfiguredEndpoint = config.endpoint || '';
  var preconfiguredApiKey = config.apikey || '';

  // =========================================================================
  // Utilities
  // =========================================================================
  function escapeAttr(str) {
    return str.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/'/g, '&#39;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function clampNumber(val, min, max, fallback) {
    var n = Number(val);
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

  /**
   * Validate an endpoint URL. Returns an error string or null if valid.
   */
  function validateEndpointUrl(val) {
    try {
      var parsed = new URL(val);
      if (parsed.protocol !== 'https:' && parsed.hostname !== 'localhost' && parsed.hostname !== '127.0.0.1') {
        return 'Use HTTPS';
      }
      return null;
    } catch (e) {
      return 'Invalid URL';
    }
  }

  // =========================================================================
  // Markdown — delegates to shared/markdown.js if available
  // =========================================================================
  function renderMd(text) {
    if (window.renderMarkdown) return window.renderMarkdown(text);
    return '<p>' + escapeHtml(text) + '</p>';
  }

  function bindCodeCopy(container) {
    if (window.attachCodeCopyListeners) window.attachCodeCopyListeners(container);
  }

  // =========================================================================
  // Storage — reads/writes connection and settings to localStorage
  // =========================================================================
  var storage = {
    _key: function (suffix) { return 'chat-' + id + '-' + suffix; },
    getEndpoint:    function () { return localStorage.getItem(this._key('endpoint')) || ''; },
    getApiKey:      function () { return localStorage.getItem(this._key('apikey')) || ''; },
    getMaxTokens:   function () { return localStorage.getItem(this._key('max-tokens')) || String(defaultMaxTokens); },
    getTemperature: function () { return localStorage.getItem(this._key('temperature')) || String(defaultTemperature); },
    getTheme:       function () { return localStorage.getItem('chat-theme') || 'dark'; },
    saveEndpoint:    function (v) { safeSaveStorage(this._key('endpoint'), v); },
    saveApiKey:      function (v) { safeSaveStorage(this._key('apikey'), v); },
    saveMaxTokens:   function (v) { safeSaveStorage(this._key('max-tokens'), v); },
    saveTemperature: function (v) { safeSaveStorage(this._key('temperature'), v); },
    saveTheme:       function (v) { safeSaveStorage('chat-theme', v); },
  };

  // =========================================================================
  // Theme
  // =========================================================================
  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
  }

  function toggleTheme() {
    var current = document.documentElement.getAttribute('data-theme') || 'dark';
    var next = current === 'dark' ? 'light' : 'dark';
    applyTheme(next);
    storage.saveTheme(next);
    updateThemeButton();
  }

  function updateThemeButton() {
    var btn = document.querySelector('.btn-theme');
    if (!btn) return;
    var theme = document.documentElement.getAttribute('data-theme') || 'dark';
    btn.textContent = theme === 'dark' ? '\u2600' : '\u263E';
    btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
  }

  // =========================================================================
  // Connection — manages endpoint, apikey, and model detection
  // =========================================================================
  var connection = {
    endpoint: '',
    apikey: '',
    detectedModel: null,

    load: function () {
      this.endpoint = storage.getEndpoint() || preconfiguredEndpoint;
      this.apikey = storage.getApiKey() || preconfiguredApiKey;
    },

    save: function () {
      storage.saveEndpoint(this.endpoint);
      storage.saveApiKey(this.apikey);
    },

    apiBase: function () {
      if (this.endpoint.endsWith('/v1')) return this.endpoint;
      return this.endpoint + '/v1';
    },

    modelName: function () {
      return this.detectedModel || '/models/weights';
    },

    check: async function (onStatus) {
      if (!this.endpoint) { onStatus('disconnected', 'Not connected'); return; }
      onStatus('checking', 'Connecting...');
      try {
        var res = await fetch(this.apiBase() + '/models', {
          headers: { 'Authorization': 'Bearer ' + this.apikey }
        });
        if (res.ok) {
          var data = await res.json();
          this.detectedModel = data.data?.[0]?.id || null;
          var label = mode === 'developer' ? (this.detectedModel || 'unknown') : 'Connected';
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
  // API Client — sends chat completion requests (decoupled from connection)
  // =========================================================================
  var currentAbortController = null;

  async function sendChatRequest(apiBase, apiKey, model, messages, maxTokens, temperature) {
    currentAbortController = new AbortController();

    var body = {
      model: model,
      messages: messages,
      max_tokens: maxTokens,
      temperature: temperature,
      stream: true,
    };

    if (stripThinking) {
      body.chat_template_kwargs = { enable_thinking: false };
    }

    var res = await fetch(apiBase + '/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + apiKey
      },
      body: JSON.stringify(body),
      signal: currentAbortController.signal,
    });

    if (!res.ok) {
      var err = await res.text();
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
  // SSE Parser — reads a ReadableStream and yields content deltas
  // =========================================================================
  async function* parseSSEStream(reader) {
    var decoder = new TextDecoder();
    var buffer = '';

    while (true) {
      var result = await reader.read();
      if (result.done) break;

      buffer += decoder.decode(result.value, { stream: true });
      var lines = buffer.split('\n');
      buffer = lines.pop();

      for (var _i = 0; _i < lines.length; _i++) {
        var line = lines[_i];
        if (!line.startsWith('data: ')) continue;
        var data = line.slice(6);
        if (data === '[DONE]') return;
        try {
          var json = JSON.parse(data);
          var delta = json.choices?.[0]?.delta?.content;
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
    var inThinking = false;
    var display = '';

    return {
      process: function (delta) {
        var i = 0;
        while (i < delta.length) {
          if (!inThinking) {
            var start = delta.indexOf('<think>', i);
            if (start !== -1) {
              display += delta.slice(i, start);
              inThinking = true;
              i = start + 7;
            } else {
              display += delta.slice(i);
              i = delta.length;
            }
          } else {
            var end = delta.indexOf('</think>', i);
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

      finalize: function () {
        return display.replace(/<think>[\s\S]*?<\/think>/g, '').trim();
      },
    };
  }

  // =========================================================================
  // Layout — builds all DOM elements
  // =========================================================================
  var layout = {
    build: function () {
      var app = document.getElementById('chat-app') || document.body;
      app.innerHTML = '';

      var themeBtn = '<button class="btn-theme" title="Toggle theme">\u2600</button>';

      if (mode === 'developer') {
        app.innerHTML += '\
          <div class="settings">\
            <div class="field endpoint">\
              <label>Endpoint URL</label>\
              <input type="text" class="js-endpoint" placeholder="https://your-pod-id-8000.proxy.runpod.net">\
            </div>\
            <div class="field apikey">\
              <label>API Key</label>\
              <input type="password" class="js-apikey" placeholder="your-api-key">\
            </div>\
            <div class="field">\
              <label>Max Tokens</label>\
              <input type="number" class="js-max-tokens" value="' + defaultMaxTokens + '" min="1" max="16384" style="width:80px">\
            </div>\
            <div class="field">\
              <label>Temperature</label>\
              <input type="number" class="js-temperature" value="' + defaultTemperature + '" min="0" max="2" step="0.1" style="width:70px">\
            </div>\
            <span class="status-badge disconnected">Not connected</span>\
            ' + themeBtn + '\
          </div>';
      } else {
        var titleHtml = config.titleAccent
          ? '<span class="accent">' + escapeHtml(config.titleAccent) + '</span> ' + escapeHtml(config.title || '')
          : escapeHtml(config.title || 'Chat');
        var subtitleHtml = config.subtitle
          ? '<div class="subtitle">' + escapeHtml(config.subtitle) + '</div>' : '';
        app.innerHTML += '\
          <div class="header">\
            <div>\
              <h1>' + titleHtml + '</h1>\
              ' + subtitleHtml + '\
            </div>\
            <div class="header-right">\
              <span class="status-badge disconnected">Not connected</span>\
              ' + themeBtn + '\
            </div>\
          </div>';
      }

      app.innerHTML += '\
        <div class="chat"></div>\
        <div class="starters"></div>\
        <div class="input-area">\
          <textarea class="chat-prompt" rows="1" placeholder="' + escapeAttr(config.placeholder || 'Type a message...') + '"></textarea>\
          <button class="btn-retry" title="Retry last message">Retry</button>\
          <button class="btn-export" title="Export conversation">Export</button>\
          <button class="btn-clear">Clear</button>\
          <button class="btn-send">Send</button>\
        </div>';

      if (mode === 'simple') {
        app.innerHTML += '\
          <div class="setup-overlay">\
            <div class="setup-box">\
              <h2>Welcome!</h2>\
              <p>Enter your connection details to get started. You only need to do this once.</p>\
              <label>Endpoint URL</label>\
              <input type="text" class="js-setup-endpoint" placeholder="https://your-pod-id-8000.proxy.runpod.net">\
              <label>API Key</label>\
              <input type="password" class="js-setup-key" placeholder="your-api-key">\
              <button class="js-setup-connect">Connect</button>\
            </div>\
          </div>';
      }
    },
  };

  // =========================================================================
  // DOM — element references (property bag populated by bind)
  // =========================================================================
  var dom = {
    chat: null, prompt: null, send: null, clear: null,
    status: null, starters: null, overlay: null,
    endpoint: null, apikey: null, maxTokens: null, temperature: null,
    retry: null, export: null,

    bind: function () {
      var q = function (sel) { return document.querySelector(sel); };
      this.chat = q('.chat');
      this.prompt = q('.chat-prompt');
      this.send = q('.btn-send');
      this.clear = q('.btn-clear');
      this.retry = q('.btn-retry');
      this.export = q('.btn-export');
      this.status = q('.status-badge');
      this.starters = q('.starters');
      this.overlay = q('.setup-overlay');
      this.endpoint = q('.js-endpoint');
      this.apikey = q('.js-apikey');
      this.maxTokens = q('.js-max-tokens');
      this.temperature = q('.js-temperature');
    },
  };

  // =========================================================================
  // Renderer — message display, status, and scrolling
  // =========================================================================
  var renderer = {
    setStatus: function (cls, text) {
      if (!dom.status) return;
      dom.status.className = 'status-badge ' + cls;
      dom.status.textContent = text;
    },

    addMessage: function (role, content) {
      var div = document.createElement('div');
      div.className = 'message ' + role;
      if (content) {
        if (role === 'assistant') {
          div.innerHTML = renderMd(content);
          bindCodeCopy(div);
        } else {
          div.textContent = content;
        }
      }
      dom.chat.appendChild(div);
      this.scrollToBottom();
      return div;
    },

    scrollToBottom: function () {
      var el = dom.chat;
      if (!el) return;
      var isNearBottom = (el.scrollHeight - el.scrollTop - el.clientHeight) < 100;
      if (isNearBottom) {
        el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
      }
    },

    forceScrollToBottom: function () {
      if (dom.chat) dom.chat.scrollTo({ top: dom.chat.scrollHeight, behavior: 'smooth' });
    },
  };

  // =========================================================================
  // Chat Controller — orchestrates messages, sending, and UI state
  // =========================================================================
  var messages = [];
  var generating = false;

  function resetChat() {
    messages = [];
    if (config.systemPrompt) {
      messages.push({ role: 'system', content: config.systemPrompt });
    }
    dom.chat.innerHTML = '';
    if (config.welcomeMessage) {
      renderer.addMessage('assistant', config.welcomeMessage);
    }
    if (dom.starters) {
      dom.starters.classList.remove('hidden');
    }
  }

  function applyProcessors(text) {
    return responseProcessors.reduce(function (t, fn) { return fn(t); }, text);
  }

  function addCopyButton(div, rawContent) {
    var btn = document.createElement('button');
    btn.className = 'msg-copy-btn';
    btn.textContent = 'Copy';
    btn.addEventListener('click', function () {
      window.copyToClipboard(rawContent, btn);
    });
    div.appendChild(btn);
  }

  async function send() {
    var text = dom.prompt.value.trim();
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

    var userMsgIndex = messages.length;
    messages.push({ role: 'user', content: text });
    renderer.addMessage('user', text);
    dom.prompt.value = '';
    dom.prompt.style.height = 'auto';

    generating = true;
    dom.send.disabled = true;

    var assistantDiv = renderer.addMessage('assistant', '');
    assistantDiv.innerHTML = '<div class="typing-indicator"><span></span><span></span><span></span></div>';

    var startTime = Date.now();
    var maxTokens = clampNumber(
      dom.maxTokens ? dom.maxTokens.value : defaultMaxTokens,
      1, 16384, defaultMaxTokens
    );
    var temp = clampNumber(
      dom.temperature ? dom.temperature.value : defaultTemperature,
      0, 2, defaultTemperature
    );

    try {
      var reader = await sendChatRequest(
        connection.apiBase(), connection.apikey, connection.modelName(),
        messages, maxTokens, temp
      );
      var filter = stripThinking ? createThinkingFilter() : null;
      var fullContent = '';
      var thinkingCleared = !stripThinking;

      for await (var delta of parseSSEStream(reader)) {
        fullContent += delta;

        if (filter) {
          var display = filter.process(delta);
          var trimmed = display.trim();
          if (trimmed && !thinkingCleared) {
            assistantDiv.innerHTML = '';
            thinkingCleared = true;
          }
          if (thinkingCleared) {
            assistantDiv.innerHTML = renderMd(trimmed);
            renderer.scrollToBottom();
          }
        } else {
          assistantDiv.innerHTML = renderMd(fullContent);
          renderer.scrollToBottom();
        }
      }

      var cleanContent = filter ? filter.finalize() : fullContent;
      cleanContent = applyProcessors(cleanContent);
      assistantDiv.innerHTML = renderMd(cleanContent);
      bindCodeCopy(assistantDiv);

      var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      var meta = document.createElement('div');
      meta.className = 'meta';
      meta.textContent = elapsed + 's';
      assistantDiv.appendChild(meta);

      addCopyButton(assistantDiv, cleanContent);

      messages.push({ role: 'assistant', content: cleanContent });

    } catch (e) {
      if (e.name === 'AbortError') {
        assistantDiv.className = 'message meta';
        assistantDiv.textContent = '(cancelled)';
      } else {
        assistantDiv.className = 'message error';
        assistantDiv.textContent = e.message;
      }
      if (messages.length > userMsgIndex && messages[userMsgIndex]?.role === 'user') {
        messages.splice(userMsgIndex, 1);
      }
    }

    currentAbortController = null;
    generating = false;
    dom.send.disabled = false;
    dom.prompt.focus();
  }

  function retry() {
    if (generating || messages.length < 2) return;
    if (messages[messages.length - 1]?.role === 'assistant') {
      messages.pop();
      var lastChild = dom.chat.lastElementChild;
      if (lastChild) dom.chat.removeChild(lastChild);
    }
    if (messages[messages.length - 1]?.role === 'user') {
      var lastUserMsg = messages.pop();
      var lastChild = dom.chat.lastElementChild;
      if (lastChild) dom.chat.removeChild(lastChild);
      dom.prompt.value = lastUserMsg.content;
      send();
    }
  }

  function exportChat() {
    var exportable = messages.filter(function (m) { return m.role !== 'system'; });
    if (exportable.length === 0) return;
    var text = exportable.map(function (m) {
      var label = m.role === 'user' ? '## You' : '## Assistant';
      return label + '\n\n' + m.content;
    }).join('\n\n---\n\n');
    var blob = new Blob([text], { type: 'text/markdown' });
    var url = URL.createObjectURL(blob);
    var a = document.createElement('a');
    a.href = url;
    a.download = 'chat-' + id + '-' + new Date().toISOString().slice(0, 10) + '.md';
    a.click();
    URL.revokeObjectURL(url);
  }

  // =========================================================================
  // Mode Initializers — separate setup paths for developer vs simple
  // =========================================================================
  function onStatusUpdate(cls, text) {
    renderer.setStatus(cls, text);
  }

  function initDeveloperMode() {
    if (dom.endpoint) {
      dom.endpoint.value = connection.endpoint;
      dom.endpoint.addEventListener('change', function () {
        var val = dom.endpoint.value.replace(/\/+$/, '');
        if (val) {
          var err = validateEndpointUrl(val);
          if (err) {
            renderer.setStatus('disconnected', err);
            return;
          }
        }
        connection.endpoint = val;
        connection.save();
        connection.check(onStatusUpdate);
      });
    }
    if (dom.apikey) {
      dom.apikey.value = connection.apikey;
      dom.apikey.addEventListener('change', function () {
        connection.apikey = dom.apikey.value;
        connection.save();
        connection.check(onStatusUpdate);
      });
    }
    if (dom.maxTokens) {
      dom.maxTokens.value = storage.getMaxTokens();
      dom.maxTokens.addEventListener('change', function () {
        storage.saveMaxTokens(dom.maxTokens.value);
      });
    }
    if (dom.temperature) {
      dom.temperature.value = storage.getTemperature();
      dom.temperature.addEventListener('change', function () {
        storage.saveTemperature(dom.temperature.value);
      });
    }
    if (connection.endpoint) {
      connection.check(onStatusUpdate);
    }
  }

  function initSimpleMode() {
    if (connection.endpoint && (connection.apikey || preconfiguredEndpoint)) {
      if (dom.overlay) dom.overlay.classList.add('hidden');
      connection.check(onStatusUpdate);
    }

    var connectBtn = document.querySelector('.js-setup-connect');
    if (connectBtn) {
      connectBtn.addEventListener('click', function () {
        var ep = document.querySelector('.js-setup-endpoint');
        var key = document.querySelector('.js-setup-key');
        if (!ep || !key) return;
        var epVal = ep.value.replace(/\/+$/, '');
        if (!epVal || !key.value) return;
        var err = validateEndpointUrl(epVal);
        if (err) {
          ep.style.borderColor = '#f87171';
          return;
        }
        ep.style.borderColor = '';
        connection.endpoint = epVal;
        connection.apikey = key.value;
        connection.save();
        if (dom.overlay) dom.overlay.classList.add('hidden');
        connection.check(onStatusUpdate);
      });
    }
  }

  var MODE_INITIALIZERS = {
    developer: initDeveloperMode,
    simple: initSimpleMode,
  };

  // =========================================================================
  // Init — entry point
  // =========================================================================
  function init() {
    applyTheme(storage.getTheme());

    layout.build();
    dom.bind();
    connection.load();

    updateThemeButton();

    var initMode = MODE_INITIALIZERS[mode] || initSimpleMode;
    initMode();

    resetChat();

    if (dom.starters && config.starters && config.starters.length > 0) {
      dom.starters.innerHTML = config.starters.map(function (s) {
        return '<button class="starter-btn">' + escapeHtml(s) + '</button>';
      }).join('');
      dom.starters.addEventListener('click', function (e) {
        if (e.target.classList.contains('starter-btn')) {
          dom.prompt.value = e.target.textContent;
          send();
        }
      });
    }

    dom.send.addEventListener('click', send);
    dom.prompt.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    });
    dom.prompt.addEventListener('input', function () {
      dom.prompt.style.height = 'auto';
      dom.prompt.style.height = Math.min(dom.prompt.scrollHeight, 200) + 'px';
    });
    dom.clear.addEventListener('click', resetChat);
    dom.retry.addEventListener('click', retry);
    dom.export.addEventListener('click', exportChat);

    var themeBtn = document.querySelector('.btn-theme');
    if (themeBtn) themeBtn.addEventListener('click', toggleTheme);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
