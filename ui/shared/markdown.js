/**
 * shared/markdown.js — Lightweight markdown parser for streaming chat.
 *
 * Exposes window.renderMarkdown(text) => HTML string.
 * Handles: fenced code blocks, inline code, bold, italic, links,
 * ordered/unordered lists, paragraphs. Tolerates incomplete/streaming input.
 * All text is HTML-escaped to prevent XSS.
 */

(function () {
  function escapeHtml(str) {
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // Inline markdown: bold, italic, inline code, links
  function renderInline(text) {
    // Inline code (must come first to protect contents)
    text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
    // Bold: **text** or __text__
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    text = text.replace(/__(.+?)__/g, '<strong>$1</strong>');
    // Italic: *text* or _text_ (not inside words for underscore)
    text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
    text = text.replace(/(?<!\w)_(.+?)_(?!\w)/g, '<em>$1</em>');
    // Links: [text](url) — only allow safe protocols
    text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener">$1</a>');
    return text;
  }

  // Parse a block of lines into a list (ul or ol), returning HTML
  function renderList(lines, ordered) {
    const tag = ordered ? 'ol' : 'ul';
    const items = lines.map(line => {
      const content = ordered
        ? line.replace(/^\d+\.\s+/, '')
        : line.replace(/^[-*]\s+/, '');
      return '<li>' + renderInline(escapeHtml(content)) + '</li>';
    });
    return '<' + tag + '>' + items.join('') + '</' + tag + '>';
  }

  window.renderMarkdown = function (text) {
    if (!text) return '';

    const output = [];

    // Step 1: Extract fenced code blocks, splitting text into code/non-code segments
    const segments = [];
    const codeBlockRe = /```(\w*)\n([\s\S]*?)(?:```|$)/g;
    let lastIndex = 0;
    let match;

    while ((match = codeBlockRe.exec(text)) !== null) {
      if (match.index > lastIndex) {
        segments.push({ type: 'text', content: text.slice(lastIndex, match.index) });
      }
      const lang = match[1] || '';
      const code = match[2];
      // Check if this block was properly closed
      const fullMatch = match[0];
      const isClosed = fullMatch.endsWith('```');
      segments.push({ type: 'code', lang: lang, content: code, closed: isClosed });
      lastIndex = codeBlockRe.lastIndex;
    }

    if (lastIndex < text.length) {
      segments.push({ type: 'text', content: text.slice(lastIndex) });
    }

    // If no segments matched, treat everything as text
    if (segments.length === 0) {
      segments.push({ type: 'text', content: text });
    }

    // Step 2: Render each segment
    for (const seg of segments) {
      if (seg.type === 'code') {
        const langLabel = seg.lang
          ? `<span class="code-lang">${escapeHtml(seg.lang)}</span>` : '';
        const copyBtn = '<button class="copy-code-btn" title="Copy code">Copy</button>';
        const header = (seg.lang || seg.closed)
          ? `<div class="code-header">${langLabel}${copyBtn}</div>` : '';
        output.push(
          `<pre>${header}<code>${escapeHtml(seg.content)}</code></pre>`
        );
        continue;
      }

      // Text segment: split into blocks by blank lines
      const blocks = seg.content.split(/\n{2,}/);

      for (const block of blocks) {
        const trimmed = block.trim();
        if (!trimmed) continue;

        const lines = trimmed.split('\n');

        // Check if all lines are unordered list items
        if (lines.every(l => /^[-*]\s+/.test(l))) {
          output.push(renderList(lines, false));
          continue;
        }

        // Check if all lines are ordered list items
        if (lines.every(l => /^\d+\.\s+/.test(l))) {
          output.push(renderList(lines, true));
          continue;
        }

        // Check for heading (# to ###)
        const headingMatch = trimmed.match(/^(#{1,3})\s+(.+)$/);
        if (headingMatch && lines.length === 1) {
          const level = headingMatch[1].length + 1; // offset by 1 so # = h2
          const tag = 'h' + Math.min(level, 4);
          output.push(`<${tag}>${renderInline(escapeHtml(headingMatch[2]))}</${tag}>`);
          continue;
        }

        // Default: paragraph (preserve single newlines as <br>)
        const paraHtml = lines
          .map(l => renderInline(escapeHtml(l)))
          .join('<br>');
        output.push('<p>' + paraHtml + '</p>');
      }
    }

    return output.join('');
  };

  // Attach copy listeners to all .copy-code-btn inside a container
  window.attachCodeCopyListeners = function (container) {
    container.querySelectorAll('.copy-code-btn').forEach(btn => {
      if (btn._copyBound) return;
      btn._copyBound = true;
      btn.addEventListener('click', () => {
        const code = btn.closest('pre')?.querySelector('code');
        if (!code) return;
        navigator.clipboard.writeText(code.textContent).then(() => {
          btn.textContent = 'Copied!';
          setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
        });
      });
    });
  };
})();
