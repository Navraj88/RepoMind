/**
 * App.js
 * Root component — owns all shared state and chat logic.
 * Composes: Header, Sidebar (IngestPanel + CollectionPanel + SourcesPanel), ChatArea.
 */

function App() {
  const { useState, useRef } = React;

  const [messages, setMessages]     = useState([]);
  const [query, setQuery]           = useState('');
  const [loading, setLoading]       = useState(false);
  const [sources, setSources]       = useState([]);
  const [refreshTick, setRefreshTick] = useState(0);

  // ── Send a chat query ────────────────────────────────────────────────────
  const sendQuery = async () => {
    const q = query.trim();
    if (!q || loading) return;

    setQuery('');
    setLoading(true);
    setSources([]);

    // Append user message
    setMessages(prev => [...prev, { role: 'user', text: q }]);

    // Build conversation history for multi-turn context
    const history = messages
      .filter(m => m.role !== 'system')
      .map(m => ({ role: m.role === 'user' ? 'user' : 'assistant', parts: [m.text] }));

    // Placeholder assistant message (shows typing indicator while streaming)
    const assistantId = Date.now();
    setMessages(prev => [...prev, { role: 'assistant', id: assistantId, text: '', streaming: true }]);

    try {
      const resp = await fetch(`${API}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, history }),
      });

      if (!resp.ok) {
        const err = await resp.json();
        throw new Error(err.detail || 'Request failed');
      }

      // Read SSE stream via fetch reader (works without EventSource for POST)
      const reader  = resp.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop(); // keep incomplete line in buffer

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          try {
            const evt = JSON.parse(line.slice(6));

            if (evt.type === 'sources') {
              setSources(evt.data);

            } else if (evt.type === 'token') {
              setMessages(prev => prev.map(m =>
                m.id === assistantId ? { ...m, text: m.text + evt.data } : m
              ));

            } else if (evt.type === 'done') {
              setMessages(prev => prev.map(m =>
                m.id === assistantId ? { ...m, streaming: false } : m
              ));

            } else if (evt.type === 'error') {
              setMessages(prev => prev.map(m =>
                m.id === assistantId
                  ? { ...m, text: `Error: ${evt.data}`, streaming: false, error: true }
                  : m
              ));
            }
          } catch {}
        }
      }

    } catch (e) {
      setMessages(prev => prev.map(m =>
        m.id === assistantId
          ? { ...m, text: `Error: ${e.message}`, streaming: false, error: true }
          : m
      ));
    }

    setLoading(false);
  };

  // ── Render ───────────────────────────────────────────────────────────────
  return React.createElement('div', { className: 'app' },

    React.createElement(Header),

    React.createElement('aside', { className: 'sidebar' },
      React.createElement(IngestPanel, {
        onIngestionDone: () => setRefreshTick(t => t + 1)
      }),
      React.createElement(CollectionPanel, { refreshTick }),
      React.createElement(SourcesPanel, { sources })
    ),

    React.createElement(ChatArea, {
      messages,
      loading,
      query,
      onQueryChange: setQuery,
      onSend: sendQuery,
    })
  );
}

// ── Mount ────────────────────────────────────────────────────────────────
ReactDOM.createRoot(document.getElementById('root')).render(
  React.createElement(App)
);
