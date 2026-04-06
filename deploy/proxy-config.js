// Production config — pre-configures the proxy endpoint so users
// don't need to enter a RunPod URL or API key.
// This file is injected before chat.js in the nginx config.
(function() {
  if (window.CHAT_CONFIG) {
    window.CHAT_CONFIG.endpoint = '/api';
    window.CHAT_CONFIG.apikey = 'proxy';
  }
})();
