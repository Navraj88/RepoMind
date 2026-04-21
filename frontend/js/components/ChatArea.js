/**
 * ChatArea.js
 * Renders the full chat interface:
 *   - Message list (user bubbles + assistant bubbles with markdown)
 *   - Typing indicator while streaming
 *   - Input bar with send button
 *
 * Props:
 *   messages      - array of message objects
 *   loading       - boolean, true while response is streaming
 *   query         - current input value
 *   onQueryChange - (value) => void
 *   onSend        - () => void
 */

function ChatArea({ messages, loading, query, onQueryChange, onSend }) {
  const { useRef, useEffect } = React;
  const messagesEndRef = useRef(null);

  // Auto-scroll to latest message
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') onSend();
  };

  // ── Empty state ────────────────────────────────────────────────────────
  const emptyState = React.createElement('div', { className: 'empty-state' },
    React.createElement('div', { className: 'empty-icon' }, '⬡'),
    React.createElement('p', null,
      'Ingest a GitHub repository on the left, then ask anything about the code.'
    ),
    React.createElement('p', { style: { fontSize: 11, opacity: .6 } },
      'Powered by Tree-sitter chunking · Qdrant hybrid search · Gemini 1.5 Flash'
    )
  );

  // ── Typing indicator ───────────────────────────────────────────────────
  const typingIndicator = React.createElement('div', { className: 'typing' },
    React.createElement('span'),
    React.createElement('span'),
    React.createElement('span')
  );

  // ── Message list ───────────────────────────────────────────────────────
  const messageList = messages.map((m, i) =>
    React.createElement('div', {
      key: m.id || i,
      className: `message message-${m.role}`
    },
      m.role === 'assistant' && React.createElement('div', { className: 'msg-meta' }, 'CodeRAG'),

      React.createElement('div', {
        className: `bubble${m.error ? ' error-banner' : ''}`
      },
        m.streaming && m.text === ''
          ? typingIndicator
          : m.role === 'assistant'
            ? renderMarkdown(m.text)
            : m.text
      )
    )
  );

  // ── Send icon SVG ──────────────────────────────────────────────────────
  const sendIcon = React.createElement('svg', {
    width: 18, height: 18, viewBox: '0 0 24 24',
    fill: 'none', stroke: 'currentColor',
    strokeWidth: '2.2', strokeLinecap: 'round', strokeLinejoin: 'round'
  },
    React.createElement('line', { x1: 22, y1: 2, x2: 11, y2: 13 }),
    React.createElement('polygon', { points: '22 2 15 22 11 13 2 9 22 2' })
  );

  return React.createElement('main', { className: 'chat-area' },

    // Messages
    React.createElement('div', { className: 'messages' },
      messages.length === 0 ? emptyState : messageList,
      React.createElement('div', { ref: messagesEndRef })
    ),

    // Input bar
    React.createElement('div', { className: 'input-bar' },
      React.createElement('input', {
        type: 'text',
        placeholder: "Ask about the codebase…  e.g. 'How does authentication work?'",
        value: query,
        onChange: e => onQueryChange(e.target.value),
        onKeyDown: handleKeyDown,
        disabled: loading,
      }),
      React.createElement('button', {
        className: 'send-btn',
        onClick: onSend,
        disabled: loading || !query.trim(),
      }, sendIcon)
    )
  );
}
