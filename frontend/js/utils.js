/**
 * utils.js
 * Lightweight markdown → React elements renderer.
 * Handles: fenced code blocks, inline code.
 */

function renderMarkdown(text) {
  const lines = text.split('\n');
  const result = [];
  let inCode = false, codeBuf = [], codeLang = '';

  lines.forEach((line, i) => {
    // Fenced code block open/close
    if (line.startsWith('```')) {
      if (!inCode) {
        inCode = true;
        codeLang = line.slice(3).trim();
        codeBuf = [];
      } else {
        result.push(
          React.createElement('pre', { key: `pre-${i}` },
            React.createElement('code', null, codeBuf.join('\n'))
          )
        );
        inCode = false;
        codeBuf = [];
      }
      return;
    }

    if (inCode) {
      codeBuf.push(line);
      return;
    }

    // Inline code: split on `...`
    const parts = line.split(/(`[^`]+`)/g).map((p, j) =>
      p.startsWith('`') && p.endsWith('`')
        ? React.createElement('code', { key: `code-${i}-${j}` }, p.slice(1, -1))
        : p
    );

    result.push(React.createElement('div', { key: `line-${i}` }, ...parts));
  });

  // Unclosed code block
  if (inCode && codeBuf.length) {
    result.push(
      React.createElement('pre', { key: 'trailing' },
        React.createElement('code', null, codeBuf.join('\n'))
      )
    );
  }

  return result;
}
