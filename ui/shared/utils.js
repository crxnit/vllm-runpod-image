/**
 * shared/utils.js — Shared utilities for all UIs.
 * Load this script before markdown.js and chat.js.
 */

/**
 * Escape HTML special characters to prevent XSS.
 */
window.escapeHtml = function (str) {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
};

/**
 * Copy text to clipboard with button feedback.
 * @param {string} text - Text to copy.
 * @param {HTMLElement} btn - Button element for visual feedback.
 */
window.copyToClipboard = function (text, btn) {
  var label = btn.textContent;
  navigator.clipboard.writeText(text).then(function () {
    btn.textContent = 'Copied!';
    setTimeout(function () { btn.textContent = label; }, 1500);
  }).catch(function () {
    btn.textContent = 'Failed';
    setTimeout(function () { btn.textContent = label; }, 1500);
  });
};
