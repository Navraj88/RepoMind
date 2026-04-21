/**
 * CollectionPanel.js
 * Displays current Qdrant collection stats (chunk count, live status)
 * and provides a reset button to wipe the collection.
 */

function CollectionPanel({ refreshTick }) {
  const { useState, useEffect, useCallback } = React;
  const [info, setInfo] = useState(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/collection/info`);
      setInfo(await r.json());
    } catch {}
  }, []);

  useEffect(() => { load(); }, [refreshTick]);

  const reset = async () => {
    if (!confirm('Delete all indexed chunks?')) return;
    await fetch(`${API}/collection/reset`, { method: 'POST' });
    load();
  };

  if (!info) return null;

  return React.createElement('div', { className: 'sidebar-section' },
    React.createElement('div', { className: 'section-label' }, 'Collection'),

    React.createElement('div', { className: 'stat-row' },
      React.createElement('span', { style: { fontSize: 11, color: 'var(--text-dim)' } }, 'Status'),
      React.createElement('span', { className: 'stat-val' }, info.exists ? '● Live' : '○ None')
    ),

    React.createElement('div', { className: 'stat-row' },
      React.createElement('span', { style: { fontSize: 11, color: 'var(--text-dim)' } }, 'Chunks'),
      React.createElement('span', { className: 'stat-val' }, info.chunk_count.toLocaleString())
    ),

    React.createElement('div', { style: { marginTop: 10 } },
      React.createElement('button', { className: 'btn btn-danger', onClick: reset },
        '⚠ Reset Collection'
      )
    )
  );
}
