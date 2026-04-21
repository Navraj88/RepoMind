/**
 * SourcesPanel.js
 * Displays the list of code chunks retrieved by hybrid search
 * for the most recent chat query, with file path, line range, and RRF score.
 */

function SourcesPanel({ sources }) {
  if (!sources.length) return null;

  return React.createElement('div', {
    className: 'sidebar-section',
    style: { flex: 1, overflowY: 'auto' }
  },
    React.createElement('div', { className: 'section-label' },
      `Retrieved Sources (${sources.length})`
    ),

    React.createElement('div', { className: 'sources-scroll' },
      sources.map((s, i) =>
        React.createElement('div', { className: 'source-item', key: i },
          React.createElement('div', { className: 'source-path' }, s.rel_path),
          React.createElement('div', { className: 'source-meta' },
            `L${s.start_line}–${s.end_line}`,
            s.symbol_name ? ` · ${s.symbol_name}` : '',
            ` · score ${(s.rrf_score * 100).toFixed(2)}`
          )
        )
      )
    )
  );
}
