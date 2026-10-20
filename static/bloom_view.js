/* Plain-language Bloom query presentation. No third-party JS. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.BloomView = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';

  const KNOWN_OUTCOMES = Object.freeze([
    'definitely_absent', 'true_positive', 'false_positive',
  ]);

  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));

  const num = (value, digits = 0) =>
    value === null || value === undefined ? '—' : Number(value).toFixed(digits);

  function isKnownOutcome(outcome) {
    return KNOWN_OUTCOMES.includes(outcome);
  }

  function hintHtml(hint) {
    if (!hint) return '';
    const choices = (hint.choices || []).map(choice =>
      `<button type="button" class="bloom-link" data-bloom-check
        data-bloom-kind="${esc(choice.kind)}"
        data-bloom-key="${esc(choice.key)}">${esc(choice.label)}</button>`).join('');
    return `<div class="bloom-hint"><p>${esc(hint.message)}</p>
      ${choices ? `<div class="bloom-hint-actions">${choices}</div>` : ''}</div>`;
  }

  function inspectionHtml(x) {
    x = x || {};
    let body;
    if (x.outcome === 'definitely_absent') {
      body = '<b>Definitely absent.</b> At least one bit is dark, so this identifier '
        + 'was never inserted. Bloom filters have no false negatives.';
    } else if (x.outcome === 'true_positive') {
      const event = x.inserted_event || {};
      const id = event.event_id ? `<code>${esc(event.event_id)}</code>` : 'an event';
      body = `<b>Possibly present, and it really was inserted by ${id}.</b> `
        + `Action <span class="badge ${esc(event.action || 'allow')}">${esc(event.action || '—')}</span>`
        + ` · score <b>${num(event.score, 0)}</b>.`;
    } else if (x.outcome === 'false_positive') {
      const sources = (x.bit_sources || []).map(source => {
        const ids = source.identifiers?.length
          ? source.identifiers.map(esc).join(', ') : 'no recorded source';
        return `<li>bit ${esc(source.position)}: ${ids}</li>`;
      }).join('');
      body = '<b>Possibly present, but it was never inserted. This is a hash '
        + 'collision (false positive).</b>'
        + `<ul>${sources}</ul>`;
    } else {
      const raw = x.outcome === undefined ? 'missing' : JSON.stringify(x.outcome);
      body = '<b>Unexpected response from server.</b> '
        + 'The query result did not match a known Bloom outcome.'
        + '<details class="bloom-raw"><summary>Raw outcome</summary>'
        + `<pre>${esc(raw)}</pre></details>`;
    }
    return `<p>${body}</p>`
      + `<p class="hint">${esc(x.kind)} · ${esc(x.key)} · k positions `
      + `[${(x.positions || []).join(', ')}] · query only, no insert.</p>`
      + hintHtml(x.hint);
  }

  return {KNOWN_OUTCOMES, isKnownOutcome, inspectionHtml, hintHtml};
});
