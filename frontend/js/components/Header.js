/**
 * Header.js
 * Top navigation bar with logo and tech stack badge.
 */

function Header() {
  return React.createElement('header', { className: 'header' },
    React.createElement('div', { className: 'logo-mark' }),
    React.createElement('span', { className: 'logo-text' },
      'Code',
      React.createElement('span', null, 'RAG')
    ),
    React.createElement('div', { className: 'header-badge' },
      'tree-sitter · qdrant · gemini'
    )
  );
}
