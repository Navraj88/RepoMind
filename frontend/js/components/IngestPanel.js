/**
 * IngestPanel.js
 * Handles GitHub URL input, triggers ingestion via POST /ingest,
 * streams live progress from GET /ingest/status (SSE),
 * and allows cancelling a running ingestion via POST /ingest/cancel.
 */

function IngestPanel({ onIngestionDone }) {
  const { useState, useRef } = React;

  const [url, setUrl]             = useState('');
  const [branch, setBranch]       = useState('');
  const [branches, setBranches]   = useState([]);
  const [loadingBranches, setLoadingBranches] = useState(false);
  const [branchError, setBranchError] = useState('');
  const [recreate, setRecreate]   = useState(false);
  const [ingesting, setIngesting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [state, setState]         = useState({ status: 'idle' });
  const esRef = useRef(null);

  const loadBranches = async () => {
    const trimmed = url.trim();
    if (!trimmed || ingesting) return;
    setLoadingBranches(true);
    setBranchError('');
    try {
      const res = await fetch(`${API}/ingest/branches?repo_url=${encodeURIComponent(trimmed)}`);
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Failed to load branches');
      const branchList = Array.isArray(data.branches) ? data.branches : [];
      setBranches(branchList);
      if (branchList.length === 0) {
        setBranch('');
      } else if (branch && !branchList.includes(branch)) {
        setBranch('');
      }
    } catch (e) {
      setBranches([]);
      setBranch('');
      setBranchError(e.message || 'Failed to load branches');
    } finally {
      setLoadingBranches(false);
    }
  };

  const startIngestion = async () => {
    if (!url.trim()) return;
    setIngesting(true);
    setCancelling(false);
    setState({ status: 'running', processed_files: 0, total_files: 0, total_chunks: 0 });

    try {
      const res = await fetch(`${API}/ingest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: url.trim(),
          branch: branch || null,
          recreate_collection: recreate,
        }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to start ingestion');
      }
    } catch (e) {
      setState({ status: 'error', error: e.message });
      setIngesting(false);
      return;
    }

    // Open SSE stream for live progress
    if (esRef.current) esRef.current.close();
    const es = new EventSource(`${API}/ingest/status`);
    esRef.current = es;

    es.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        setState(data);
        if (data.status === 'done') {
          setIngesting(false);
          setCancelling(false);
          es.close();
          onIngestionDone();
        }
        if (data.status === 'cancelled' || data.status === 'error') {
          setIngesting(false);
          setCancelling(false);
          es.close();
        }
      } catch {}
    };

    es.onerror = () => {
      setIngesting(false);
      setCancelling(false);
      es.close();
    };
  };

  const cancelIngestion = async () => {
    setCancelling(true);
    try {
      await fetch(`${API}/ingest/cancel`, { method: 'POST' });
    } catch (e) {
      setCancelling(false);
    }
  };

  const pct = state.total_files > 0
    ? Math.round((state.processed_files / state.total_files) * 100)
    : 0;

  const dotClass = {
    idle: 'dot-idle', running: 'dot-running', done: 'dot-done',
    error: 'dot-error', cancelled: 'dot-error',
  }[state.status] || 'dot-idle';

  return React.createElement(React.Fragment, null,

    // ── URL input + buttons ─────────────────────────────────────
    React.createElement('div', { className: 'sidebar-section' },
      React.createElement('div', { className: 'section-label' }, 'Ingest Repository'),

      React.createElement('div', { className: 'field', style: { marginBottom: 8 } },
        React.createElement('label', null, 'GitHub URL or owner/repo'),
        React.createElement('input', {
          type: 'url',
          placeholder: 'https://github.com/owner/repo',
          value: url,
          onChange: e => {
            setUrl(e.target.value);
            setBranches([]);
            setBranch('');
            setBranchError('');
          },
          onKeyDown: e => e.key === 'Enter' && !ingesting && startIngestion(),
          onBlur: () => {
            if (!url.trim() || branches.length > 0 || loadingBranches || ingesting) return;
            loadBranches();
          },
          disabled: ingesting,
        })
      ),

      React.createElement('div', { className: 'field', style: { marginBottom: 8 } },
        React.createElement('label', null, 'Branch (optional)'),
        React.createElement('div', { style: { display: 'flex', gap: 8 } },
          React.createElement('select', {
            value: branch,
            onChange: e => setBranch(e.target.value),
            disabled: ingesting || loadingBranches || !url.trim(),
            style: { flex: 1 },
          },
            React.createElement('option', { value: '' }, 'Default branch'),
            ...branches.map(b =>
              React.createElement('option', { key: b, value: b }, b)
            )
          ),
          React.createElement('button', {
            className: 'btn',
            type: 'button',
            disabled: ingesting || loadingBranches || !url.trim(),
            onClick: loadBranches,
          }, loadingBranches ? 'Loading...' : 'Load branches')
        ),
        branchError && React.createElement('div', {
          className: 'error-banner',
          style: { marginTop: 6 }
        }, branchError)
      ),

      React.createElement('label', { className: 'checkbox-row' },
        React.createElement('input', {
          type: 'checkbox',
          checked: recreate,
          onChange: e => setRecreate(e.target.checked),
          disabled: ingesting,
        }),
        'Recreate collection (clears existing data)'
      ),

      // Ingest + Cancel buttons
      React.createElement('div', { style: { marginTop: 10, display: 'flex', gap: 8 } },
        React.createElement('button', {
          className: 'btn btn-primary',
          style: { flex: 1 },
          disabled: ingesting || !url.trim(),
          onClick: startIngestion,
        }, ingesting ? '⏳ Ingesting…' : '⚡ Ingest'),

        ingesting && React.createElement('button', {
          className: 'btn btn-danger',
          style: { flex: '0 0 auto', width: 'auto', padding: '9px 14px' },
          disabled: cancelling,
          onClick: cancelIngestion,
        }, cancelling ? '⏳' : '✕ Cancel')
      )
    ),

    // ── Progress status ─────────────────────────────────────────
    React.createElement('div', { className: 'sidebar-section' },
      React.createElement('div', { className: 'section-label' }, 'Ingestion Status'),

      React.createElement('div', { style: { fontSize: 12, marginBottom: 6 } },
        React.createElement('span', { className: `status-dot ${dotClass}` }),
        React.createElement('span', {
          style: { fontFamily: 'var(--font-mono)', textTransform: 'capitalize' }
        }, state.status)
      ),

      state.current_file && React.createElement('div', {
        style: {
          fontFamily: 'var(--font-mono)', fontSize: 10,
          color: 'var(--text-dim)', wordBreak: 'break-all', marginBottom: 4
        }
      }, state.current_file),

      state.status === 'running' && React.createElement(React.Fragment, null,
        React.createElement('div', { className: 'progress-bar-track' },
          React.createElement('div', {
            className: 'progress-bar-fill',
            style: { width: `${pct}%` }
          })
        ),
        React.createElement('div', { className: 'progress-stats' },
          React.createElement('span', null, `${state.processed_files}/${state.total_files} files`),
          React.createElement('span', null, `${state.total_chunks} chunks`)
        )
      ),

      state.status === 'done' && React.createElement('div', { className: 'progress-stats' },
        React.createElement('span', { style: { color: 'var(--accent)' } }, `✓ ${state.processed_files} files`),
        React.createElement('span', null, `${state.total_chunks} chunks indexed`)
      ),

      state.status === 'cancelled' && React.createElement('div', {
        style: { fontSize: 11, color: 'var(--warn)', fontFamily: 'var(--font-mono)', marginTop: 4 }
      }, `✕ Cancelled after ${state.processed_files} files · ${state.total_chunks} chunks saved`),

      state.status === 'error' && state.error && React.createElement('div', {
        className: 'error-banner', style: { marginTop: 6 }
      }, state.error)
    )
  );
}