/* Radar Lab UI. No third-party JS; SVG/CSS only. */
let state = null;
let selectedId = null;
let queue = [];
let demoEvents = [];
let playing = false;
let paused = false;
let generation = 0;
let aprioriModel = [];
let aprioriSelectedKey = null;
let aprioriScope = 'all';
let aprioriLimit = 5;
let aprioriBusy = false;
let aprioriBusyMessage = 'Mining patterns…';
let aprioriError = '';
let aprioriNotice = 'Ready. Choose a setting or run the guided demo.';
let aprioriGuidedStep = 0;
let aprioriPresetReady = false;
let guidedIndex = 0;
let guidedOpen = false;
let lastTotal = 0;
let lastBeep = 0;
let dgimFilter = 'all';
let dgimSearch = '';
let dgimLoading = false;
let dgimError = '';
let dgimUpdateNote = '';
let evidenceBusy = false;
let evidenceError = '';
let audioCtx = null;
let soundOn = localStorage.getItem('radar-sound') !== 'off';

const $ = id => document.getElementById(id);
const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pct = v => v === null || v === undefined ? '—' : `${(Number(v) * 100).toFixed(1)}%`;
const num = (v, d = 0) => v === null || v === undefined ? '—' : Number(v).toFixed(d);
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  };
  const response = await fetch(`/api/${path}`, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed');
  return data;
}
const apiGet = path => api(path);

function toast(message) {
  const el = $('toast');
  el.textContent = message;
  el.classList.remove('hidden');
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => el.classList.add('hidden'), 6500);
}

function fmtTime(seconds) {
  seconds = Math.max(0, Number(seconds) || 0);
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
           : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
const eventTime = e => fmtTime((e.time || 0) - (state?.dataset_start || 0));
const liveTime = () => fmtTime((state?.clock || 0) - (state?.dataset_start || 0));

function eventView(e) {
  const mode = $('shadow-view')?.value || 'full';
  if (mode === 'baseline' && e.baseline) {
    return {score: e.baseline.score, action: e.baseline.action, mode: 'baseline'};
  }
  return {score: e.score, action: e.action, mode: 'full'};
}

const COLORS = ['#6554d9','#3b7dd8','#229578','#bc8837','#de6378','#8e5cc7','#2f9bb3','#a2632f'];
const DECISION_BOX = {allow: 'stat-allow', review: 'stat-review', block: 'stat-block'};

function beep(action) {
  if (!soundOn || performance.now() - lastBeep < 90) return;
  const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
  if (!AudioContextCtor) return;
  lastBeep = performance.now();
  try {
    audioCtx = audioCtx || new AudioContextCtor();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    const now = audioCtx.currentTime;
    const block = action === 'block';
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = block ? 'sawtooth' : 'sine';
    osc.frequency.setValueAtTime(block ? 300 : action === 'review' ? 580 : 880, now);
    gain.gain.setValueAtTime(0.0001, now);
    gain.gain.exponentialRampToValueAtTime(block ? 0.07 : 0.05, now + 0.01);
    gain.gain.exponentialRampToValueAtTime(0.0001, now + (block ? 0.24 : 0.13));
    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start(now);
    osc.stop(now + (block ? 0.26 : 0.15));
  } catch {}
}

function pulseDecision(action) {
  const node = $(DECISION_BOX[action]);
  if (!node) return;
  node.classList.remove('flash-allow', 'flash-review', 'flash-block');
  void node.offsetWidth;
  node.classList.add(`flash-${action}`);
  clearTimeout(node.flashTimer);
  node.flashTimer = setTimeout(() => node.classList.remove(`flash-${action}`), 720);
  beep(action);
}

/* ---------- rendering ---------- */
function render(s) {
  const previousTotal = lastTotal;
  state = s;
  if (!state) return;
  lastTotal = Number(state.total) || 0;
  $('total').textContent = state.total;
  $('active').textContent = state.active;
  $('allowed').textContent = state.counts.allow || 0;
  $('reviewed').textContent = state.counts.review || 0;
  $('blocked').textContent = state.counts.block || 0;
  if (lastTotal > previousTotal && state.events?.[0]) pulseDecision(state.events[0].action);
  $('clock').textContent = liveTime();
  $('window-length').textContent = `${state.window_seconds || state.dgim_config?.window || 60}s`;
  $('dataset-mode').textContent = state.dataset?.name || 'synthetic';
  $('event-count').textContent = state.total;
  $('latency').textContent = `Local scoring: ${num(state.average_latency_ms, 3)} ms avg`;
  ['review', 'block'].forEach(k => {
    const range = $(`${k}-range`);
    if (range && document.activeElement !== range) range.value = state.thresholds[k];
    $(`${k}-value`).textContent = state.thresholds[k];
  });
  const scale = document.querySelector('.risk-scale');
  if (scale) scale.style.borderImage = `linear-gradient(90deg,var(--green) 0 ${state.thresholds.review}%,var(--amber) ${state.thresholds.review}% ${state.thresholds.block}%,var(--red) ${state.thresholds.block}% 100%) 1`;
  renderChart();
  renderEvents();
  renderInspection();
  renderBloom();
  renderDgim();
  renderApriori();
  renderEvidence();
}

function renderChart() {
  const series = state.series || [];
  const max = Math.max(5, ...series.map(b => b.total));
  $('chart').innerHTML = series.length ? series.map(b => {
    const flagged = b.total ? (b.flagged / b.total * 100) : 0;
    return `<div class="chart-bar" title="${esc(b.time)}s: ${b.total} events, ${b.flagged} flagged">
      <div class="bar-total" style="height:${Math.max(2, b.total / max * 100)}%">
        <div class="bar-flagged" style="height:${flagged}%"></div></div>
      <span class="bar-label">${esc(b.time)}</span></div>`;
  }).join('') : '<span class="hint" style="margin:auto">Activity appears as events arrive</span>';
}

function renderEvents() {
  const filter = $('filter').value;
  const rows = (state.events || []).filter(e => filter === 'all' || eventView(e).action === filter);
  const empty = $('empty');
  empty.classList.toggle('hidden', rows.length > 0);
  if (!rows.length && state.total) empty.innerHTML = '<div>⌁</div><h3>No matching events</h3><p>Choose another decision filter.</p>';
  else if (!state.total) empty.innerHTML = '<div>⌁</div><h3>Your event stream starts here</h3><p>Press Play or run a scenario.</p>';
  if (!(state.events || []).some(e => e.id === selectedId)) selectedId = state.events?.[0]?.id || null;
  $('events').innerHTML = rows.map(e => {
    const v = eventView(e);
    const tag = v.mode === 'baseline' ? `<small>learned: ${num(v.score, 1)}</small>` : '';
    return `<tr data-id="${esc(e.id)}" class="${selectedId === e.id ? 'selected' : ''}" tabindex="0" aria-label="Inspect ${esc(e.id)}">
      <td><strong>${esc(e.type)}</strong><small>${esc(e.account)}</small>${tag}</td>
      <td>${eventTime(e)}</td>
      <td><div class="score"><span class="mini-track"><i style="width:${Math.min(100, v.score)}%"></i></span>${num(v.score, 1)}</div></td>
      <td><span class="badge ${esc(v.action)}">${esc(v.action)}</span></td></tr>`;
  }).join('');
  const sh = state.shadow;
  if ($('shadow-summary') && sh) {
    $('shadow-summary').textContent = sh.compared
      ? `Ablation: ${sh.flipped} of ${sh.compared} decisions change without Bloom/DGIM (${sh.allow_to_flagged} allow→flagged). Baseline view differs on ${sh.baseline_view?.flipped_vs_full ?? '—'} events.`
      : 'No events yet.';
  }
}

function renderInspection() {
  const e = (state.events || []).find(x => x.id === selectedId);
  if (!e) {
    $('inspection').innerHTML = '<div class="empty"><div>◎</div><h3>Every score has a story</h3><p>Select an event to see its signals,<br>window counts and score breakdown.</p></div>';
    $('current-score').textContent = '—';
    $('current-action').textContent = 'allow';
    $('current-action').className = 'badge allow';
    $('score-breakdown').innerHTML = '';
    return;
  }
  const v = eventView(e);
  const b = e.bloom || {}, g = e.dgim || {}, sh = e.shadow || {};
  const baseline = e.baseline;
  $('current-score').textContent = num(v.score, 1);
  $('current-action').textContent = v.action;
  $('current-action').className = `badge ${v.action}`;
  $('score-chip').textContent = v.mode === 'baseline' ? 'learned baseline' : 'Bloom/DGIM';
  renderBreakdown(e, v);
  const reasons = (e.reasons || []).map(r => `<div class="reason"><b>+${r.points}</b><span>${esc(r.reason)}</span></div>`).join('');
  const shadowLine = sh.base_score !== undefined
    ? `<p class="hint">Exact-rules score <b>${sh.base_score} ${sh.base_action}</b> → full <b>${e.score} ${e.action}</b>${sh.flipped ? ' · Bloom/DGIM flipped the decision' : ''}.</p>` : '';
  const baselineLine = baseline
    ? `<p class="hint">Learned baseline: <b>${num(baseline.score, 1)} ${baseline.action}</b> (probability ${num(baseline.probability, 3)}); label-free JSON model.</p>` : '';
  $('inspection').innerHTML = `
    <div class="inspect-top"><div><strong>${num(v.score, 1)}<small> / 100</small></strong><p class="hint">${v.mode === 'baseline' ? 'Learned score, not a calibrated probability' : 'Heuristic rule score, not a probability'}</p></div>
      <span class="badge ${v.action}">${v.action}</span></div>
    ${reasons || '<p class="hint">No configured risk indicators were triggered.</p>'}
    <div class="feature-list">
      <span>Device accounts <b>${e.features?.device_accounts ?? '—'}</b></span>
      <span>IP accounts <b>${e.features?.ip_accounts ?? '—'}</b></span>
      <span>Card attempts <b>${e.features?.card_attempts ?? '—'}</b></span>
      <span>Device trials <b>${e.features?.device_trials ?? '—'}</b></span>
      <span>Bloom device <b>${esc(b.device_result || '—')}</b></span>
      <span>Bloom card <b>${esc(b.card_result || '—')}</b></span>
      <span>DGIM est <b>${g.device_est ?? '—'}</b> (exact ${g.device_exact ?? '—'})</span>
      <span>Bloom inserted <b>${b.inserted ? 'yes (high-risk)' : 'no'}</b></span>
    </div>
    ${shadowLine}${baselineLine}
    <p class="hint">Window counts at event time. DGIM is last ${g.window || state.dgim_config?.window || 60} stream elements. Score capped at 100. Review ≥ ${e.review_threshold ?? state.thresholds.review}, block ≥ ${e.block_threshold ?? state.thresholds.block}.</p>
    <div class="inspect-id">${esc(e.id)} · <button class="link-button" data-entity-kind="device" data-entity-value="${esc(e.device)}">${esc(e.device)}</button>
      · <button class="link-button" data-entity-kind="card" data-entity-value="${esc(e.card)}">${esc(e.card)}</button>
      · <button class="link-button" data-entity-kind="account" data-entity-value="${esc(e.account)}">${esc(e.account)}</button><br>
      Evaluation truth: ${esc(e.label)} (never used in scoring)</div>`;
}

function renderBreakdown(e, view) {
  const bar = $('score-breakdown');
  if (view.mode === 'baseline') {
    const score = Math.max(0, Math.min(100, view.score));
    bar.innerHTML = `<i style="width:${score}%;background:var(--blue)"></i>`;
    return;
  }
  const reasons = e.reasons || [];
  const total = Math.max(1, reasons.reduce((sum, r) => sum + r.points, 0));
  bar.innerHTML = reasons.length ? reasons.map((r, i) => `<i title="${esc(r.reason)} (+${r.points})" style="width:${r.points / total * 100}%;background:${COLORS[i % COLORS.length]}"></i>`).join('') : '';
}

function renderBloom() {
  if (!state.bloom) return;
  const bd = state.bloom.device, bc = state.bloom.card;
  $('bloom-params').textContent = `m=${bd.m || bd.size}, k=${bd.k || bd.num_hashes}`;
  const inspection = state.inspection;
  const kind = inspection?.kind;
  const positions = inspection?.positions || [];
  renderBitGrid($('bloom-device-grid'), bd.bits || [], kind === 'device' ? positions : []);
  renderBitGrid($('bloom-card-grid'), bc.bits || [], kind === 'card' ? positions : []);
  renderQueryBits('bloom-device-bits', kind === 'device' ? inspection : null, bd);
  renderQueryBits('bloom-card-bits', kind === 'card' ? inspection : null, bc);
  if (inspection) renderBloomInspection(inspection);
  else {
    $('bloom-query-result').className = 'bloom-result';
    $('bloom-query-result').innerHTML = '<p class="hint">No query yet.</p>';
  }
}

function renderBitGrid(container, bits, positions) {
  if (!container) return;
  const shown = bits.slice(0, 512);
  const pos = new Set((positions || []).filter(p => p < shown.length));
  container.innerHTML = shown.map((bit, i) => {
    const cls = pos.has(i) ? (bit ? 'both' : 'query-dark') : bit ? 'set' : '';
    return `<span class="${cls}" title="bit ${i}: ${bit ? 'lit' : 'dark'}${pos.has(i) ? ' · current query' : ''}"></span>`;
  }).join('');
  const next = container.nextElementSibling;
  if (next?.classList.contains('bit-grid-note')) next.remove();
  if (bits.length > shown.length) {
    container.insertAdjacentHTML('afterend', `<p class="hint bit-grid-note">Showing first ${shown.length} of ${bits.length} bits.</p>`);
  }
}

function renderQueryBits(id, inspection, filter) {
  const el = $(id);
  if (!el) return;
  if (!inspection) { el.innerHTML = ''; return; }
  const bits = filter.bits || [];
  el.innerHTML = (inspection.positions || []).map(p => {
    const lit = Boolean(bits[p]);
    return `<span class="bloom-bit ${lit ? 'lit' : 'dark'}">bit ${p} · ${lit ? 'lit' : 'dark'}</span>`;
  }).join('');
}

function renderBloomInspection(x) {
  const el = $('bloom-query-result');
  const view = window.BloomView;
  if (!view) {
    el.className = 'bloom-result unexpected';
    el.textContent = 'Unexpected response from server.';
    console.error('Bloom view helper is unavailable.', x);
    return;
  }
  const known = view.isKnownOutcome(x.outcome);
  el.className = `bloom-result ${known ? x.outcome : 'unexpected'}`;
  el.innerHTML = view.inspectionHtml(x);
  if (!known) console.error('Unexpected Bloom inspection outcome.', x.outcome, x);
}

const dgimView = () => window.DgimView;

function renderDgim() {
  const view = dgimView();
  const d = state?.dgim;
  if (!view || !d) return;
  const threshold = d.threshold ?? state.dgim_config?.threshold ?? 5;
  const windowSize = d.window_size || state.dgim_config?.window || 60;
  const windowEl = $('dgim-window-range');
  const thresholdEl = $('dgim-threshold-range');
  if (windowEl && document.activeElement !== windowEl) windowEl.value = windowSize;
  if (thresholdEl && document.activeElement !== thresholdEl) thresholdEl.value = threshold;
  if ($('dgim-window-value')) $('dgim-window-value').textContent = windowSize;
  if ($('dgim-threshold-value')) $('dgim-threshold-value').textContent = threshold;
  const search = $('dgim-search');
  if (search && document.activeElement !== search) search.value = dgimSearch;
  document.querySelectorAll('[data-dgim-filter]').forEach(button => {
    button.classList.toggle('active', button.dataset.dgimFilter === dgimFilter);
  });
  $('dgim-entity-count').textContent = (d.ranked || []).length;
  renderDgimVerdict(d, threshold, windowSize);
  renderDgimEntityList(d, threshold);
  renderDgimStream(d);
  renderDgimMetrics(d);
  const chart = $('dgim-chart');
  if (chart) chart.innerHTML = svgDgimEntityChart(d, threshold);
}

function renderDgimVerdict(d, threshold, windowSize) {
  const view = dgimView();
  const box = $('dgim-verdict');
  if (!box) return;
  box.className = 'dgim-verdict card normal';
  if (dgimLoading) {
    box.className = 'dgim-verdict card';
    $('dgim-verdict-icon').textContent = '…';
    $('dgim-verdict-title').textContent = 'Loading this entity…';
    $('dgim-verdict-result').textContent = 'DGIM is reading the stream and buckets.';
    $('dgim-verdict-meta').textContent = '';
    return;
  }
  if (dgimError) {
    box.className = 'dgim-verdict card error';
    $('dgim-verdict-icon').textContent = '!';
    $('dgim-verdict-title').textContent = 'Could not update the DGIM view.';
    $('dgim-verdict-result').textContent = dgimError;
    $('dgim-verdict-meta').textContent = 'Try the control again; the last good state is still shown.';
    return;
  }
  if (!(d.recent || []).length) {
    box.className = 'dgim-verdict card normal';
    $('dgim-verdict-icon').textContent = '○';
    $('dgim-verdict-title').textContent = d.entity ? 'No events for this entity yet.' : 'No DGIM streams yet.';
    $('dgim-verdict-result').textContent = d.entity
      ? 'DGIM has nothing to score for this entity.'
      : 'Run a scenario to populate per-device and per-card streams.';
    $('dgim-verdict-meta').textContent = 'The window counts this entity\'s events, not seconds.';
    return;
  }
  const banner = view.bannerFor(d);
  const result = view.resultFor(d);
  box.className = `dgim-verdict card ${banner.tone}`;
  $('dgim-verdict-icon').textContent = banner.icon;
  $('dgim-verdict-title').textContent = banner.title;
  $('dgim-verdict-result').textContent = result.text;
  let meta = `Window N = ${windowSize} events · burst threshold = ${threshold}. The window counts this entity's events, not seconds.`;
  if (d.all_normal && (d.ranked || []).length) meta += ` ${view.ALL_NORMAL_MESSAGE}`;
  if (dgimUpdateNote) meta = `${dgimUpdateNote} ${meta}`;
  $('dgim-verdict-meta').textContent = meta;
}

function renderDgimEntityList(d, threshold) {
  const view = dgimView();
  const list = $('dgim-entity-list');
  if (!list) return;
  let rows = (d.ranked || []).map(row => ({
    ...row, status: view.statusFor(row.estimate, threshold).key,
  }));
  if (dgimFilter !== 'all') rows = rows.filter(row => row.status === dgimFilter);
  const query = dgimSearch.trim().toLowerCase();
  if (query) rows = rows.filter(row => String(row.name).toLowerCase().includes(query));
  if (!rows.length) {
    list.innerHTML = `<div class="dgim-empty"><b>${(d.ranked || []).length ? 'No entity matches.' : 'No entities yet.'}</b>
      ${(d.ranked || []).length ? 'Clear the filter or search for another ID.' : 'Run a scenario to create device and card streams.'}</div>`;
  } else {
    list.innerHTML = rows.map(row => {
      const status = view.STATUS[row.status] || view.STATUS.normal;
      const selected = d.entity === row.name && d.kind === row.kind;
      return `<button type="button" class="dgim-entity ${selected ? 'selected' : ''}"
        data-dgim-kind="${esc(row.kind)}" data-dgim-name="${esc(row.name)}"
        aria-pressed="${selected ? 'true' : 'false'}">
        <span><span class="dgim-entity-name">${esc(row.name)}</span>
          <span class="dgim-entity-meta"><span class="dgim-kind-tag">${esc(row.kind)}</span>
            ${row.events} events · ${row.bucket_count} buckets</span></span>
        <span class="dgim-entity-right"><span class="dgim-estimate-num">${num(row.estimate)}</span>
          <span class="dgim-status ${status.tone}"><span aria-hidden="true">${status.icon}</span> ${status.label}</span></span>
      </button>`;
    }).join('');
  }
  $('dgim-ranking-note').textContent = d.all_normal && (d.ranked || []).length
    ? view.ALL_NORMAL_MESSAGE
    : 'Sorted by current DGIM estimate, highest first.';
}

function dgimCellTitle(row) {
  const bit = row.cancel ? 'cancel event, not scored' : row.bit ? 'suspicious (1)' : 'clean (0)';
  const signal = (row.signals || []).length ? row.signals.join(', ') : 'none';
  const time = fmtTime((row.time || 0) - (state?.dataset_start || 0));
  return `Event ${row.id} · ${time} · ${row.type} · action ${row.action} · score ${num(row.score, 1)} · ${bit} · signal: ${signal}`;
}

function renderDgimStream(d) {
  const view = dgimView();
  const cells = $('dgim-stream-cells');
  const track = $('dgim-buckets-track');
  const inner = $('dgim-stream-inner');
  const caption = $('dgim-buckets-caption');
  const none = $('dgim-no-buckets');
  if (!cells || !track) return;
  $('dgim-kind-chip').textContent = d.entity
    ? `${view.kindLabel(d.kind)} · ${d.entity}` : 'no entity';
  const rows = d.recent || [];
  if (!rows.length) {
    inner.style.width = 'auto';
    cells.innerHTML = `<div class="dgim-empty"><b>${d.entity ? 'No events for this entity yet.' : 'No entity selected.'}</b>
      ${d.entity ? 'Choose another entity or run a scenario.' : 'Pick an entity from the ranked list.'}</div>`;
    track.innerHTML = '';
    if (caption) caption.textContent = '';
    if (none) none.classList.add('hidden');
    if ($('dgim-cancel-note')) $('dgim-cancel-note').classList.add('hidden');
    return;
  }
  const n = rows.length;
  const cell = n <= 40 ? 20 : n <= 80 ? 15 : n <= 150 ? 11 : n <= 300 ? 7 : 4;
  const gap = n <= 150 ? 2 : 1;
  inner.style.setProperty('--dgim-cell', `${cell}px`);
  inner.style.setProperty('--dgim-gap', `${gap}px`);
  inner.style.width = `${n * cell + (n - 1) * gap}px`;
  cells.innerHTML = rows.map((row, index) => {
    const cls = row.cancel ? 'cancel' : row.bit ? 'suspicious' : 'clean';
    const newest = index === rows.length - 1 ? ' newest' : '';
    return `<button type="button" class="dgim-cell ${cls}${newest}" data-dgim-event="${esc(row.id)}"
      title="${esc(dgimCellTitle(row))}" aria-label="${esc(dgimCellTitle(row))}"></button>`;
  }).join('');
  const startPos = rows[0].pos;
  const endPos = rows[n - 1].pos;
  const spans = view.bucketSpans(d.view_buckets || [], startPos, endPos);
  const colors = ['#6554d9','#3b7dd8','#229578','#bc8837','#de6378','#8e5cc7','#2f9bb3'];
  track.innerHTML = spans.map(span => {
    const left = span.left * (cell + gap);
    const width = Math.max(cell, span.width * (cell + gap) - gap);
    const color = colors[span.index % colors.length];
    return `<div class="dgim-bucket-box" style="left:${left}px;width:${width}px;background:${color}"
      title="Bucket ${span.index + 1}: size ${span.size}, covers event positions ${span.startPos}–${span.endPos}">${span.size}</div>`;
  }).join('');
  if (caption) {
    caption.textContent = spans.length
      ? `DGIM does not keep these cells. It keeps only these ${spans.length} boxes and estimates the count from them.`
      : '';
  }
  const cancelNote = $('dgim-cancel-note');
  if (cancelNote) cancelNote.classList.toggle('hidden', !rows.some(row => row.cancel));
  if (none) none.classList.toggle('hidden', Number(d.exact) !== 0 || spans.length > 0);
}

function renderDgimMetrics(d) {
  const has = Boolean(d.entity && (d.recent || []).length);
  $('dgim-estimate').textContent = has ? num(d.estimate) : '—';
  $('dgim-exact').textContent = has ? num(d.exact) : '—';
  const difference = has ? Number(d.estimate) - Number(d.exact) : null;
  $('dgim-difference').textContent = has
    ? `${difference > 0 ? '+' : ''}${difference}` : '—';
}

function svgDgimEntityChart(d, threshold) {
  const view = dgimView();
  const rows = d.recent || [];
  if (!rows.length) return '<div class="dgim-empty"><b>No events to chart.</b>Run a scenario or choose another entity.</div>';
  const w = 680, h = 250, padL = 40, padR = 18, padT = 26, padB = 38;
  const maxY = Math.max(1, threshold, ...rows.map(row => Math.max(row.estimate, row.exact)));
  const maxX = Math.max(1, rows.length - 1);
  const x = index => padL + (index / maxX) * (w - padL - padR);
  const y = value => h - padB - (value / maxY) * (h - padT - padB);
  const step = maxX ? (w - padL - padR) / maxX : 0;
  const grid = [0, 0.25, 0.5, 0.75, 1].map(f =>
    `<line x1="${padL}" y1="${y(maxY * f)}" x2="${w - padR}" y2="${y(maxY * f)}" stroke="var(--line)"/>
     <text x="${padL - 6}" y="${y(maxY * f) + 3}" text-anchor="end" class="dgim-chart-label">${Math.round(maxY * f)}</text>`).join('');
  const shades = view.burstSpans(rows, threshold).map(span => {
    const left = Math.max(padL, x(span.start) - (span.start ? step / 2 : 0));
    const right = Math.min(w - padR, x(span.end) + (span.end < rows.length - 1 ? step / 2 : 0));
    return `<rect x="${left}" y="${padT}" width="${Math.max(2, right - left)}" height="${h - padT - padB}"
      fill="rgba(222,99,120,.12)"/>`;
  }).join('');
  const estimate = rows.map((row, index) =>
    `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(row.estimate).toFixed(1)}`).join(' ');
  const exact = rows.map((row, index) =>
    `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(row.exact).toFixed(1)}`).join(' ');
  const pinIndex = rows.findIndex(row => row.fired);
  let pin = '';
  if (pinIndex >= 0) {
    const px = x(pinIndex);
    const py = y(rows[pinIndex].estimate);
    const anchor = px > w - 150 ? 'end' : 'start';
    const labelX = anchor === 'end' ? px - 7 : px + 7;
    pin = `<line x1="${px}" y1="${py}" x2="${px}" y2="${h - padB}" stroke="var(--red)" stroke-width="1.5" stroke-dasharray="3 3"/>
      <circle cx="${px}" cy="${py}" r="5" fill="var(--red)" stroke="var(--card)" stroke-width="2"/>
      <text x="${labelX}" y="${Math.max(padT, py - 10)}" text-anchor="${anchor}" class="dgim-chart-pin-label">burst detected here</text>`;
  }
  const thresholdY = y(threshold);
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="DGIM estimate, exact count and burst threshold over this entity's event positions">
    ${shades}${grid}
    <line x1="${padL}" y1="${thresholdY}" x2="${w - padR}" y2="${thresholdY}" stroke="var(--amber)" stroke-width="1.5" stroke-dasharray="7 4"/>
    <text x="${w - padR}" y="${Math.max(padT, thresholdY - 5)}" text-anchor="end" class="dgim-chart-label">burst threshold ${threshold}</text>
    <line x1="${padL}" y1="${h - padB}" x2="${w - padR}" y2="${h - padB}" stroke="var(--line)"/>
    <line x1="${padL}" y1="${padT}" x2="${padL}" y2="${h - padB}" stroke="var(--line)"/>
    <path d="${estimate}" fill="none" stroke="var(--red)" stroke-width="2"/>
    <path d="${exact}" fill="none" stroke="var(--blue)" stroke-width="1.8" stroke-dasharray="5 4"/>
    ${pin}
    <text x="${padL}" y="${h - 8}" class="dgim-chart-label">oldest event</text>
    <text x="${w - padR}" y="${h - 8}" text-anchor="end" class="dgim-chart-label">newest event</text>
    <g transform="translate(${padL + 4},${padT - 8})">
      <line x1="0" y1="0" x2="18" y2="0" stroke="var(--red)" stroke-width="2"/>
      <text x="23" y="3" class="dgim-chart-label">DGIM estimate</text>
      <line x1="125" y1="0" x2="143" y2="0" stroke="var(--blue)" stroke-width="1.8" stroke-dasharray="5 4"/>
      <text x="148" y="3" class="dgim-chart-label">exact count</text>
      <line x1="228" y1="0" x2="246" y2="0" stroke="var(--amber)" stroke-width="1.5" stroke-dasharray="7 4"/>
      <text x="251" y="3" class="dgim-chart-label">burst threshold</text>
    </g>
  </svg>`;
}

const aprioriView = () => window.AprioriView;

function aprioriTerm(text, key) {
  const tip = aprioriView()?.TERM_TIPS?.[key] || '';
  return `<span class="apriori-term" role="button" tabindex="0" data-glossary="${esc(key)}" data-tip="${esc(tip)}">${esc(text)}</span>`;
}

function aprioriChip(item, match = false) {
  const view = aprioriView();
  const tone = view.signalTone(item);
  return `<span class="signal-chip tone-${tone}${match ? ' match' : ''}">${esc(view.signalLabel(item))}</span>`;
}

function aprioriChipList(items, matches = null) {
  const chips = (items || []).map(item => aprioriChip(item, matches ? matches.has(item) : false));
  if (!chips.length) return 'no signals';
  if (chips.length === 1) return chips[0];
  if (chips.length === 2) return `${chips[0]} and ${chips[1]}`;
  return `${chips.slice(0, -1).join(', ')} and ${chips[chips.length - 1]}`;
}

function aprioriStrength(strength, lift) {
  const liftText = lift === null || lift === undefined ? '' : `<span class="pattern-lift">${esc(aprioriView().formatLift(lift))} vs chance</span>`;
  return `<span class="strength-badge ${strength.tone}" title="Lift ${esc(aprioriView().formatLift(lift))}"><span aria-hidden="true">${strength.icon}</span> ${esc(strength.label)}</span>${liftText}`;
}

function renderApriori() {
  const view = aprioriView();
  if (!view || !state?.apriori_config) return;
  renderAprioriConfig();
  renderAprioriCounts();
  const includeLabels = Boolean($('apriori-label-mode')?.checked);
  const scope = view.scopeData(state, aprioriScope);
  aprioriModel = view.buildPatternModel(scope.itemsets, scope.rules, scope.analyzed, {includeLabels});
  if (aprioriSelectedKey && !aprioriModel.some(pattern => pattern.key === aprioriSelectedKey)) aprioriSelectedKey = null;
  const preset = view.presetForConfig(state.apriori_config.min_support, state.apriori_config.min_confidence);
  const presetLabel = preset ? view.PRESETS[preset].label : 'Custom thresholds';
  const scopeLabel = aprioriScope === 'abuse' ? `${scope.analyzed} abuse-labelled events` : `${scope.analyzed} events`;
  $('apriori-result-meta').textContent = `${scopeLabel} · ${presetLabel} · ${aprioriModel.length} pattern${aprioriModel.length === 1 ? '' : 's'}`;
  renderAprioriPatterns();
  renderAprioriExamples();
  renderAprioriLevels(scope);
  renderAprioriSplit(view.timeSplitView(state, includeLabels));
  renderAprioriEval(includeLabels);
  renderAprioriFlow();
  renderAprioriStatus();
}

function renderAprioriConfig() {
  const view = aprioriView();
  document.querySelectorAll('[data-preset-description]').forEach(el => {
    const preset = view.PRESETS[el.dataset.presetDescription];
    if (preset) el.textContent = preset.description;
  });
  const support = Number(state.apriori_config.min_support);
  const confidence = Number(state.apriori_config.min_confidence || 0);
  const supportEl = $('apriori-support');
  const confidenceEl = $('apriori-confidence');
  if (supportEl && document.activeElement !== supportEl) supportEl.value = Math.max(1, Math.round(support * 100));
  if (confidenceEl && document.activeElement !== confidenceEl) confidenceEl.value = Math.round(confidence * 100);
  $('apriori-support-value').textContent = view.formatPercent(support);
  $('apriori-confidence-value').textContent = `${Math.round(confidence * 100)}%`;
  const preset = view.presetForConfig(support, confidence);
  document.querySelectorAll('input[name="apriori-preset"]').forEach(input => {
    input.checked = input.value === preset;
  });
  $('apriori-preset-note').textContent = preset
    ? `${view.PRESETS[preset].label} is selected.`
    : `Custom thresholds are active: ${view.formatPercent(support)} support and ${Math.round(confidence * 100)}% confidence.`;
}

function renderAprioriCounts() {
  const view = aprioriView();
  const cls = state.apriori_class || {};
  const total = Number(cls.all_events?.analyzed ?? state.analyzed ?? 0);
  const abuse = Number(cls.abuse_only?.analyzed ?? 0);
  const prevalence = cls.prevalence === null || cls.prevalence === undefined
    ? (total ? abuse / total : 0)
    : Number(cls.prevalence);
  $('apriori-all-count').textContent = `${total} event${total === 1 ? '' : 's'}`;
  $('apriori-abuse-count').textContent = `${abuse} abuse event${abuse === 1 ? '' : 's'} (${view.formatPercent(prevalence)})`;
  document.querySelectorAll('input[name="apriori-scope"]').forEach(input => {
    input.checked = input.value === aprioriScope;
  });
}

function renderAprioriPatterns() {
  const view = aprioriView();
  const shown = aprioriLimit === null ? aprioriModel : aprioriModel.slice(0, aprioriLimit);
  const list = $('apriori-patterns');
  if (!aprioriModel.length) {
    list.innerHTML = `<div class="apriori-empty"><span aria-hidden="true">○</span><p>No patterns at this strictness. Try Broad, or lower the support.</p></div>`;
  } else {
    list.innerHTML = shown.map(pattern => {
      const sentence = pattern.kind === 'rule'
        ? `When ${aprioriChipList(pattern.antecedent)} ${pattern.antecedent.length === 1 ? 'happens' : 'happen'}, ${aprioriChipList(pattern.consequent)} ${pattern.consequent.length === 1 ? 'also happens' : 'also happen'} in <b>${view.formatPercent(pattern.confidence)}</b> of cases (seen in <b>${pattern.count ?? '—'}</b> of ${pattern.total} events, <b>${view.formatLift(pattern.lift)}</b> more often than chance).`
        : pattern.items.length === 1
          ? `${aprioriChipList(pattern.items)} appears in <b>${pattern.count ?? '—'}</b> of ${pattern.total} events.`
          : `${aprioriChipList(pattern.items)} appear together in <b>${pattern.count ?? '—'}</b> of ${pattern.total} events.`;
      return `<div class="apriori-pattern${aprioriSelectedKey === pattern.key ? ' selected' : ''}" data-pattern-key="${esc(pattern.key)}" role="button" tabindex="0" aria-pressed="${aprioriSelectedKey === pattern.key}" aria-label="${esc(pattern.sentence)}">
        <div class="apriori-sentence">${sentence}</div>
        <div class="apriori-pattern-meta">${aprioriStrength(pattern.strength, pattern.lift)}<span class="pattern-click-hint">Click for examples</span></div>
      </div>`;
    }).join('');
  }
  const showAll = $('apriori-show-all');
  const canExpand = aprioriModel.length > 5 && aprioriLimit !== null;
  showAll.classList.toggle('hidden', !canExpand);
  if (canExpand) showAll.textContent = `Show all ${aprioriModel.length}`;
}

function renderAprioriExamples() {
  const view = aprioriView();
  const panel = $('apriori-examples');
  const pattern = aprioriModel.find(item => item.key === aprioriSelectedKey);
  if (!pattern) {
    panel.classList.add('hidden');
    panel.innerHTML = '';
    return;
  }
  panel.classList.remove('hidden');
  const wanted = new Set(pattern.items);
  const matches = (state.events || []).filter(event => {
    if (aprioriScope === 'abuse' && event.label !== 'abuse') return false;
    return pattern.items.every(item => (event.items || []).includes(item));
  });
  const rows = matches.slice(0, 10).map(event => `<tr>
    <td><b>${eventTime(event)}</b><small>${esc(event.id)}</small></td>
    <td>${esc(event.account)}<small>${esc(event.device)}</small></td>
    <td><span class="badge ${esc(event.action)}">${esc(event.action)}</span></td>
    <td>${num(event.score, 1)}</td>
    <td class="apriori-example-chips">${(event.items || []).map(item => aprioriChip(item, wanted.has(item))).join(' ') || '<span class="hint">No signals</span>'}</td>
  </tr>`).join('');
  panel.innerHTML = `<div class="apriori-example-head"><div><h3>Events that contain this pattern</h3>
      <p class="hint"><b>${pattern.count ?? '—'} of ${pattern.total} events</b> contain this pattern. Showing ${matches.length} matching event${matches.length === 1 ? '' : 's'} from the latest ${(state.events || []).length} loaded.</p></div>
      <button class="icon-button" id="apriori-example-close" aria-label="Close matching events">×</button></div>
    ${matches.length ? `<div class="table-scroll table-scroll-tall"><table class="table-compact apriori-example-table"><thead><tr><th>TIME</th><th>ACCOUNT / DEVICE</th><th>ACTION</th><th>SCORE</th><th>SIGNALS IN THIS EVENT</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<p class="hint">No matching events are in the currently loaded window.</p>'}`;
}

function renderAprioriLevels(scope) {
  const levels = {};
  (scope.levels || []).forEach(level => { levels[level.level] = level; });
  const level1 = levels[1] || {candidates: 0, frequent: 0};
  const level2 = levels[2] || {candidates: 0, frequent: 0};
  const level3 = levels[3] || {candidates: 0, frequent: 0};
  const candidates = aprioriTerm('candidates', 'candidate');
  $('apriori-levels').innerHTML = `
    <li><span class="level-tag">Level 1</span><p>${level1.frequent} single signals appear often enough (${level1.candidates} ${candidates}, ${level1.frequent} kept).</p></li>
    <li><span class="level-tag">Level 2</span><p>Pairs built only from kept signals (${level2.candidates} ${candidates}, ${level2.frequent} kept).</p></li>
    <li><span class="level-tag">Level 3</span><p>Triples built only from kept pairs (${level3.candidates} ${candidates}, ${level3.frequent} kept).</p></li>`;
}

function renderAprioriSplit(split) {
  const view = aprioriView();
  $('apriori-split-chip').textContent = split.train_n || split.test_n
    ? `${split.train_n} first · ${split.test_n} second` : 'waiting for events';
  $('apriori-split-meta').textContent = split.train_n || split.test_n
    ? `We mine the first ${split.train_n} events, then check the same rules on the last ${split.test_n}. This check is separate from the pattern list above.`
    : 'Run mixed traffic to compare the first and second half.';
  if (!split.rows.length) {
    $('apriori-split').innerHTML = `<div class="apriori-empty"><span aria-hidden="true">○</span><p>${esc(split.note || 'No label-free patterns were checked in both halves.')}</p></div>`;
  } else {
    $('apriori-split').innerHTML = split.rows.map(check => {
      const later = check.test_confidence === null || check.test_confidence === undefined
        ? 'Not enough data' : view.formatPercent(check.test_confidence);
      const premiseCount = check.test_antecedent_count === null || check.test_antecedent_count === undefined
        ? '' : ` The premise appeared ${check.test_antecedent_count} of ${split.test_n} times in the second half.`;
      return `<div class="apriori-split-row">
        <div class="split-rule">${aprioriChipList(check.antecedent)} <span class="arrow" aria-hidden="true">→</span> ${aprioriChipList(check.consequent)}</div>
        <div class="split-metrics">
          <div><span>Found in first half</span><b>${view.formatPercent(check.train_confidence)}</b></div>
          <div><span>Holds in second half</span><b class="${check.test_confidence === null || check.test_confidence === undefined ? 'muted' : ''}">${later}</b></div>
          <span class="verdict ${check.verdict.tone}"><span aria-hidden="true">${check.verdict.icon}</span> ${esc(check.verdict.label)}</span>
        </div>
        <p class="split-detail">${esc(check.verdict.detail)}${esc(premiseCount)}</p>
      </div>`;
    }).join('');
  }
  $('apriori-split-hidden').textContent = split.hidden
    ? `${split.hidden} label-containing pattern${split.hidden === 1 ? ' is' : 's are'} hidden. Turn on Advanced (evaluation only) to inspect label rules.`
    : '';
}

function renderAprioriEval(includeLabels) {
  const view = aprioriView();
  const target = $('apriori-eval');
  if (!includeLabels) {
    target.innerHTML = '<p class="hint">Turn on label-as-item mode to show label-containing rules.</p>';
    return;
  }
  const data = state.apriori_eval || {};
  const total = Number(data.analyzed ?? state.analyzed ?? 0);
  const rules = (data.rules || []).filter(rule => view.hasLabel([...(rule.antecedent || []), ...(rule.consequent || [])]));
  if (!rules.length) {
    target.innerHTML = '<p class="hint">No label-containing rules at this strictness.</p>';
    return;
  }
  target.innerHTML = `<h3>Label-containing rules</h3>` + rules.slice(0, 8).map(rule => `
    <div class="apriori-eval-rule">
      <div class="eval-sentence">${aprioriChipList(rule.antecedent)} <span class="arrow" aria-hidden="true">→</span> ${aprioriChipList(rule.consequent)}</div>
      <p class="hint">${view.formatPercent(rule.confidence)} ${aprioriTerm('confidence', 'confidence')} · ${view.formatLift(rule.lift)} ${aprioriTerm('lift', 'lift')} · ${rule.count ?? '—'} of ${total} events</p>
    </div>`).join('');
}

function renderAprioriFlow() {
  document.querySelectorAll('#apriori-flow [data-flow-step]').forEach(item => {
    const step = Number(item.dataset.flowStep);
    item.classList.toggle('done', step <= aprioriGuidedStep);
    item.classList.toggle('current', step === aprioriGuidedStep + 1 && aprioriGuidedStep < 4);
    if (step === aprioriGuidedStep + 1 && aprioriGuidedStep < 4) item.setAttribute('aria-current', 'step');
    else item.removeAttribute('aria-current');
  });
}

function renderAprioriStatus() {
  const el = $('apriori-status');
  if (!el) return;
  el.className = 'apriori-status';
  if (aprioriBusy) {
    el.classList.add('loading');
    el.innerHTML = `<span class="spinner" aria-hidden="true"></span> ${esc(aprioriBusyMessage)}`;
    return;
  }
  if (aprioriError) {
    el.classList.add('error');
    el.innerHTML = `<span><b>Could not mine patterns.</b> ${esc(aprioriError)}</span> <button class="button secondary" id="apriori-retry">Try again</button>`;
    return;
  }
  el.textContent = aprioriNotice;
}

async function aprioriApply(support, confidence, notice) {
  if (aprioriBusy) return false;
  aprioriBusy = true;
  aprioriBusyMessage = 'Mining patterns…';
  aprioriError = '';
  renderAprioriStatus();
  try {
    const next = await api('apriori', {min_support: support, min_confidence: confidence});
    aprioriBusy = false;
    aprioriNotice = notice || `Mined at ${aprioriView().formatPercent(support)} support and ${Math.round(confidence * 100)}% confidence.`;
    aprioriSelectionReset();
    render(next);
    return true;
  } catch (error) {
    aprioriBusy = false;
    aprioriError = error.message || 'Request failed.';
    renderAprioriStatus();
    return false;
  }
}

function aprioriSelectionReset() {
  aprioriSelectedKey = null;
  aprioriLimit = 5;
}

async function aprioriApplyPreset(key, options = {}) {
  const preset = aprioriView().PRESETS[key];
  if (!preset) return false;
  if (options.resetScope) aprioriScope = 'all';
  aprioriPresetReady = true;
  return aprioriApply(preset.minSupport, preset.minConfidence,
    `${preset.label}: ${preset.description}${options.silent ? '' : ' Patterns updated.'}`);
}

async function aprioriUseSuggested() {
  const stateView = aprioriView();
  const suggested = state?.apriori_config?.suggested_min_support ?? 0.05;
  const confidence = Number($('apriori-confidence').value) / 100;
  return aprioriApply(suggested, confidence,
    `Using the suggested ${stateView.formatPercent(suggested)} support so rare abuse patterns are not hidden.`);
}

function selectAprioriPattern(key) {
  aprioriSelectedKey = key;
  renderAprioriPatterns();
  renderAprioriExamples();
}

function openAprioriGlossary(key) {
  const glossary = $('apriori-glossary');
  if (glossary) glossary.open = true;
  const entry = document.getElementById(`glossary-${key}`);
  if (!entry) return;
  entry.classList.remove('glossary-focus');
  void entry.offsetWidth;
  entry.classList.add('glossary-focus');
  entry.scrollIntoView({behavior: 'smooth', block: 'center'});
  clearTimeout(entry._focusTimer);
  entry._focusTimer = setTimeout(() => entry.classList.remove('glossary-focus'), 2200);
}

async function aprioriGuidedDemo() {
  const view = aprioriView();
  aprioriPresetReady = true;
  aprioriScope = 'all';
  aprioriGuidedStep = 0;
  aprioriSelectionReset();
  aprioriError = '';
  renderAprioriFlow();
  try {
    aprioriBusy = true;
    aprioriBusyMessage = 'Step 1 of 4: running mixed traffic…';
    renderAprioriStatus();
    const scenarioState = await api('scenario_run', {name: 'mixed'});
    aprioriBusy = false;
    render(scenarioState);
    aprioriGuidedStep = 1;
    renderAprioriFlow();

    const preset = view.PRESETS.balanced;
    aprioriBusy = true;
    aprioriBusyMessage = 'Step 2 of 4: choosing Balanced…';
    renderAprioriStatus();
    const balancedState = await api('apriori', {min_support: preset.minSupport, min_confidence: preset.minConfidence});
    aprioriBusy = false;
    aprioriNotice = `Step 2 complete: Balanced uses ${view.formatPercent(preset.minSupport)} support and ${Math.round(preset.minConfidence * 100)}% confidence.`;
    render(balancedState);
    aprioriGuidedStep = 2;
    renderAprioriFlow();

    aprioriGuidedStep = 3;
    if (aprioriModel[0]) {
      aprioriSelectedKey = aprioriModel[0].key;
      renderAprioriPatterns();
      renderAprioriExamples();
    }
    aprioriNotice = aprioriModel[0]
      ? `Step 3 complete: the top pattern is “${aprioriModel[0].sentence}”`
      : 'Step 3 complete: no pattern met the Balanced thresholds.';
    renderAprioriFlow();
    renderAprioriStatus();

    aprioriGuidedStep = 4;
    renderAprioriFlow();
    const splitCard = document.querySelector('.apriori-split-card');
    if (aprioriModel[0]) $('apriori-patterns').scrollIntoView({behavior: 'smooth', block: 'center'});
    if (splitCard) setTimeout(() => splitCard.scrollIntoView({behavior: 'smooth', block: 'center'}), 650);
    aprioriNotice = 'Step 4 complete: compare “Found in first half” with “Holds in second half” and read the verdict badge.';
    renderAprioriStatus();
    toast('Apriori guided demo complete: pattern selected and time-split check shown.');
  } catch (error) {
    aprioriBusy = false;
    aprioriError = error.message || 'Request failed.';
    renderAprioriStatus();
    toast(error.message);
  }
}

function renderEvidence() {
  const ev = state?.evaluation;
  const status = ev?.ladder_models || state?.ladder_status;
  const stale = $('ladder-stale');
  if (status?.message) {
    stale.textContent = status.message;
    stale.classList.remove('hidden');
  } else {
    stale.textContent = '';
    stale.classList.add('hidden');
  }
  $('baseline-source').textContent = baselineSource();
  if (!ev) {
    $('evidence-split').textContent = 'Replay the synthetic demo to compute the comparison.';
    $('evidence-threshold').textContent = 'review ≥ —';
    $('ladder-abuse-count').textContent = 'Replay the synthetic demo to compute the held-out comparison.';
    $('ladder-few-warning').textContent = '';
    $('evidence-ladder').innerHTML = '<tr><td colspan="8" class="hint">Playback state only. Replay to compute the ladder.</td></tr>';
    $('ladder-pr-chart').innerHTML = '';
    $('ladder-model-cards').innerHTML = '';
    return;
  }
  const rows = ev.ladder || [];
  const diag = ev.ladder_diagnostics || {};
  const fewAbuse = Boolean(diag.few_abuse_events);
  $('recall-1pct-header').classList.toggle('muted-col', fewAbuse);
  $('evidence-split').textContent = `Test split: last ${ev.split.test_n} of ${ev.split.train_n + ev.split.test_n} labeled events · ${ev.method}`;
  $('evidence-threshold').textContent = `review ≥ ${ev.thresholds.review}`;
  $('ladder-abuse-count').textContent = `${diag.test_positives ?? 0} abuse events in the held-out set · ${ev.split.test_n} test events.`;
  $('ladder-few-warning').textContent = diag.warning || '';
  const ci = value => value && value.low !== null && value.low !== undefined
    ? `[${num(value.low, 3)}, ${num(value.high, 3)}]`
    : (diag.bootstrap?.done ? '—' : 'computing…');
  const recall = (row, key) => row.available ? pct(row[key]?.recall) : '—';
  const cell = (row, html) => row.available ? html : '—';
  $('evidence-ladder').innerHTML = rows.map(row => {
    const label = `<b>${esc(row.label)}</b>${row.reference ? '<small class="ci">reference</small>' : ''}`;
    return `<tr class="${row.reference ? 'reference-row' : ''}">
      <td>${label}</td>
      <td>${row.trained ? 'yes' : 'no'}</td>
      <td>${row.uses_rule_score ? 'yes' : 'no'}</td>
      <td>${row.uses_bloom_dgim ? 'yes' : 'no'}</td>
      <td>${cell(row, `<b>${num(row.pr_auc, 3)}</b><small class="ci">95% CI ${ci(row.pr_auc_ci)}</small>`)}</td>
      <td class="${fewAbuse ? 'muted-col' : ''}">${cell(row, recall(row, 'recall_at_1pct_fpr'))}</td>
      <td>${cell(row, `${recall(row, 'recall_at_5pct_fpr')}<small class="ci">95% CI ${ci(row.recall_at_5pct_fpr_ci)}</small>`)}</td>
      <td>${cell(row, `${pct(row.precision)} / ${pct(row.recall)} / ${pct(row.false_positive_rate)}`)}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="8" class="hint">No ladder rows available.</td></tr>';
  $('ladder-pr-chart').innerHTML = svgLadderPrChart(rows);
  renderLadderModelCards(status);
  const ablation = ev.ablation || [];
  const maxFlip = Math.max(1, ...ablation.map(a => a.flips));
  $('ablation').innerHTML = ablation.length ? ablation.map(a => `<div class="pattern"><div style="display:flex;justify-content:space-between;gap:10px"><span>${esc(a.rule)}</span><b style="margin:0">${a.flips} flip${a.flips === 1 ? '' : 's'} / ${a.events} events</b></div><div style="height:7px;background:var(--line);border-radius:5px;margin-top:5px;overflow:hidden"><i style="display:block;height:100%;width:${a.flips / maxFlip * 100}%;background:var(--red)"></i></div></div>`).join('') : '<p class="hint">No rule-ablation data on the test split.</p>';
}

function renderLadderModelCards(status) {
  const names = {raw: 'LR-raw', raw_bd: 'LR-raw+BD', all: 'LR-all'};
  const models = (status?.models || []).filter(model => names[model.name]);
  $('ladder-model-cards').innerHTML = models.map(model => {
    if (!model.available) return `<div class="model-card"><b>${names[model.name]}</b><p class="hint">model unavailable — retrain</p></div>`;
    const card = model.card || {};
    const range = card.time_range;
    const top = (card.top_weights || []).map(item => `${esc(item.feature)} ${num(item.weight, 3)}`).join(' · ');
    return `<div class="model-card"><b>${names[model.name]}</b>
      <p class="hint">C=${esc(card.C)} · ${card.train_events ?? '—'} rows trained (${card.train_positives ?? '—'} abuse) · test ${card.test_events ?? '—'} (${card.test_positives ?? '—'} abuse)</p>
      <p class="hint">Training time: ${range ? `${fmtTime(range.start_ts)}–${fmtTime(range.end_ts)}` : '—'}</p>
      <p class="hint"><b>Top weights by magnitude:</b> ${top || '—'}</p></div>`;
  }).join('') || '<p class="hint">Learned models are unavailable. Run python tools/train_ladder.py.</p>';
}

function baselineSource() {
  const status = state?.evaluation?.ladder_models || state?.ladder_status;
  const count = status?.models?.filter(model => model.available).length ?? 0;
  return `Models: the running app loads models/ladder_*.json (${count}/3 available), trained offline by tools/train_ladder.py on the synthetic fixture with a strict event-time split.`;
}

function svgLadderPrChart(rows) {
  const wanted = [
    ['exact_rules', 'Exact rules', '#6554d9'],
    ['full_engine', 'Full engine', '#3b7dd8'],
    ['lr_raw', 'LR-raw', '#229578'],
    ['lr_raw_bd', 'LR-raw+BD', '#bc8837'],
    ['lr_all', 'LR-all (reference)', '#de6378'],
  ];
  const series = wanted.map(([id, label, color]) => {
    const row = rows.find(item => item.id === id);
    return {label, color, points: row?.available ? (row.pr_curve || []) : []};
  }).filter(item => item.points.length);
  if (!series.length) return '<p class="hint">No positive test events, so the PR curves are undefined.</p>';
  const w = 660, h = 240, pad = 38;
  const x = r => pad + r * (w - pad * 1.3);
  const y = p => h - pad - p * (h - pad * 1.5);
  const grid = [0, 0.25, 0.5, 0.75, 1].map(f =>
    `<line x1="${pad}" y1="${y(f)}" x2="${w - 16}" y2="${y(f)}" stroke="var(--line)"/>` +
    `<text x="${pad - 6}" y="${y(f) + 3}" font-size="9" fill="var(--muted)" text-anchor="end">${f}</text>`).join('');
  const paths = series.map(item => {
    const d = item.points.map((point, index) =>
      `${index ? 'L' : 'M'}${x(point.recall).toFixed(1)},${y(point.precision).toFixed(1)}`).join(' ');
    return `<path d="${d}" fill="none" stroke="${item.color}" stroke-width="2.1"/>`;
  }).join('');
  const legend = series.map(item =>
    `<span><i style="background:${item.color}"></i>${esc(item.label)}</span>`).join('');
  return `<div class="chart-legend">${legend}</div>
    <svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Evidence ladder precision recall curves">
      ${grid}
      <line x1="${pad}" y1="${h - pad}" x2="${w - 16}" y2="${h - pad}" stroke="var(--line)"/>
      <line x1="${pad}" y1="${pad / 2}" x2="${pad}" y2="${h - pad}" stroke="var(--line)"/>
      <text x="${w / 2}" y="${h - 8}" font-size="10" fill="var(--muted)" text-anchor="middle">recall</text>
      <text x="8" y="16" font-size="10" fill="var(--muted)">precision</text>
      ${paths}
    </svg>`;
}

function svgLineChart(samples) {
  if (!samples.length) return '<p class="hint">No samples.</p>';
  const w = 560, h = 210, pad = 32;
  const maxX = Math.max(...samples.map(s => s.pos));
  const maxY = Math.max(1, ...samples.map(s => Math.max(s.exact, s.estimate)));
  const x = v => pad + (v / maxX) * (w - pad * 1.4);
  const y = v => h - pad - (v / maxY) * (h - pad * 1.6);
  const exact = samples.map((s, i) => `${i ? 'L' : 'M'}${x(s.pos).toFixed(1)},${y(s.exact).toFixed(1)}`).join(' ');
  const est = samples.map((s, i) => `${i ? 'L' : 'M'}${x(s.pos).toFixed(1)},${y(s.estimate).toFixed(1)}`).join(' ');
  const grid = [0.25, 0.5, 0.75].map(f => `<line x1="${pad}" y1="${y(maxY * f)}" x2="${w - 10}" y2="${y(maxY * f)}" stroke="var(--line)"/>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="DGIM estimate versus exact count">
    ${grid}
    <line x1="${pad}" y1="${h - pad}" x2="${w - 10}" y2="${h - pad}" stroke="var(--line)"/>
    <line x1="${pad}" y1="${pad / 2}" x2="${pad}" y2="${h - pad}" stroke="var(--line)"/>
    <text x="${w / 2}" y="${h - 6}" font-size="9" fill="var(--muted)" text-anchor="middle">stream position</text>
    <path d="${exact}" fill="none" stroke="var(--purple)" stroke-width="2"/>
    <path d="${est}" fill="none" stroke="var(--red)" stroke-width="2" stroke-dasharray="5 4"/>
    <text x="${w - 120}" y="16" font-size="9" fill="var(--purple)">exact</text>
    <text x="${w - 70}" y="16" font-size="9" fill="var(--red)">estimate</text>
  </svg>`;
}

/* ---------- playback ---------- */
function updateControls() {
  $('play').disabled = playing || !queue.length;
  $('pause').disabled = !playing;
  $('pause').textContent = paused ? 'Resume' : 'Pause';
}
const delay = () => Math.max(8, 1010 - Number($('speed').value || 650));

async function pump(token) {
  let failed = false;
  while (playing && token === generation) {
    if (paused) { await sleep(120); continue; }
    if (!queue.length) break;
    const event = queue.shift();
    try {
      const s = await api('event', {...event, light: true});
      if (token !== generation) return;
      render(s);
    } catch (error) {
      toast(error.message);
      failed = true;
      break;
    }
    $('run-status').textContent = `Replaying · ${queue.length} events remaining`;
    updateControls();
    await sleep(delay());
  }
  if (token !== generation) return;
  playing = false;
  paused = false;
  updateControls();
  $('run-status').textContent = failed ? 'Replay stopped · see the error message' : 'Replay complete · full evaluation refreshed';
  if (!failed) await refreshFull();
}

async function refreshFull() {
  try {
    const s = await apiGet('state');
    if (s) render(s);
  } catch (error) { toast(error.message); }
}

async function loadEvidence() {
  if (evidenceBusy) return;
  evidenceBusy = true;
  evidenceError = '';
  renderEvidence();
  try {
    const ev = await apiGet('evidence');
    if (state) {
      state.evaluation = ev;
      if (ev.ladder_models) state.ladder_status = ev.ladder_models;
    }
    if (ev?.ladder_diagnostics?.bootstrap?.done) {
      $('ladder-few-warning').textContent = ev.ladder_diagnostics.warning || '';
    }
    renderEvidence();
  } catch (error) {
    evidenceError = error.message;
    $('ladder-abuse-count').textContent = `Could not load bootstrap intervals: ${error.message}`;
    toast(error.message);
  } finally {
    evidenceBusy = false;
  }
}

function stopPlayback() {
  generation++;
  playing = false;
  paused = false;
  queue = [];
  updateControls();
}

async function loadSynthetic() {
  stopPlayback();
  try {
    const response = await api('replay', {dataset: 'synthetic'});
    demoEvents = response.events || [];
    queue = demoEvents.slice();
    selectedId = null;
    render(await apiGet('state?light=1'));
    $('run-status').textContent = `Loaded ${queue.length} synthetic events (time-contiguous). Press Play.`;
    updateControls();
  } catch (error) { toast(error.message); }
}

async function runScenario(name, instant = false) {
  stopPlayback();
  try {
    if (instant) {
      const s = await api('scenario_run', {name});
      selectedId = s.events?.[0]?.id || null;
      render(s);
      $('run-status').textContent = `Instant scenario run · ${name}`;
      return s;
    }
    await api('reset', {});
    const response = await api('scenario', {name});
    queue = (response.events || []).slice();
    playing = true;
    paused = false;
    const token = ++generation;
    updateControls();
    await pump(token);
  } catch (error) {
    toast(error.message);
    stopPlayback();
  }
}

async function resetSession() {
  stopPlayback();
  selectedId = null;
  $('entity-panel').classList.add('hidden');
  try {
    const s = await api('reset', {});
    render(s);
    queue = demoEvents.slice();
    $('run-status').textContent = queue.length
      ? `Session reset · ${queue.length}-event synthetic demo ready. Press Play.`
      : 'Session reset · synthetic demo not loaded';
  } catch (error) { toast(error.message); }
  updateControls();
}

/* ---------- demos ---------- */
async function bloomRunDemo() {
  try {
    const s = await api('scenario_run', {name: 'card_testing'});
    render(s);
    switchTab('bloom');
    toast('Bloom demo: card-testing burst inserted high-risk devices/cards. Query an inserted identifier.');
  } catch (error) { toast(error.message); }
}

async function dgimRunDemo(pattern = 'random') {
  try {
    const chart = $('dgim-demo-chart');
    if (chart) chart.innerHTML = '<p class="hint">Running 1000-bit stream…</p>';
    const data = await apiGet(`dgim_demo?n=1000&window=100&pattern=${encodeURIComponent(pattern)}`);
    const summary = data.summary;
    if (chart) chart.innerHTML = svgLineChart(data.samples);
    $('dgim-demo-storage').textContent = `${summary.max_buckets} buckets vs ${summary.exact_storage} exact`;
    $('dgim-demo-summary').textContent = `Counted 1s over the last ${summary.window} bits with at most `
      + `${summary.max_buckets} buckets instead of ${summary.exact_storage} stored bits; average error `
      + `${pct(summary.avg_rel_error)}, worst ${pct(summary.max_rel_error)}, bound ~50%. `
      + `(${summary.pattern} stream)`;
    switchTab('dgim');
  } catch (error) { toast(error.message); }
}

async function loadDgimEntity(kind, name) {
  dgimLoading = true;
  dgimError = '';
  dgimUpdateNote = '';
  renderDgim();
  try {
    const s = await apiGet(`state?entity=${encodeURIComponent(name)}&entity_kind=${encodeURIComponent(kind)}`);
    dgimLoading = false;
    render(s);
  } catch (error) {
    dgimLoading = false;
    dgimError = error.message;
    renderDgim();
    toast(error.message);
  }
}

async function updateDgimConfig(payload, note) {
  const d = state?.dgim || {};
  dgimLoading = true;
  dgimError = '';
  dgimUpdateNote = note || '';
  renderDgim();
  try {
    const body = {...payload};
    if (d.entity) {
      body.dgim_entity = d.entity;
      body.dgim_entity_kind = d.kind;
    }
    const s = await api('config', body);
    dgimLoading = false;
    render(s);
  } catch (error) {
    dgimLoading = false;
    dgimError = error.message;
    renderDgim();
    toast(error.message);
  }
}

/* ---------- navigation and preferences ---------- */
async function switchTab(name) {
  document.querySelectorAll('.nav').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('hidden', p.id !== `tab-${name}`));
  if (name === 'evidence') {
    await loadEvidence();
  } else if (name !== 'live') {
    if (!state?.evaluation || !state?.apriori_levels) await refreshFull();
  }
  if (name === 'apriori' && !aprioriPresetReady) {
    aprioriPresetReady = true;
    if (aprioriView()?.presetForConfig(state?.apriori_config?.min_support, state?.apriori_config?.min_confidence) !== 'balanced') {
      await aprioriApplyPreset('balanced', {silent: true});
    }
  }
}

function applyPrefs() {
  const dark = localStorage.getItem('radar-theme') === 'dark';
  document.body.classList.toggle('dark', dark);
  $('theme').textContent = dark ? 'Light' : 'Dark';
  $('sound').textContent = soundOn ? 'Sound on' : 'Sound off';
}

/* ---------- guided demo ---------- */
const guidedSteps = [
  {caption: '<b>Welcome to Radar Lab.</b> This is an independent educational simulation of payment and free-trial abuse detection, not Stripe software. We will demonstrate live scoring, then Bloom, DGIM, Apriori and temporal evidence in under five minutes.', action: async () => switchTab('live')},
  {caption: '<b>Step 1 — load the synthetic demo.</b> The committed fixture carries five event types and authored labels. Labels are carried only for evaluation; they never enter scoring.', action: async () => { await loadSynthetic(); switchTab('live'); }},
  {caption: '<b>Step 2 — everyday payments.</b> Eight independent events stay allowed. The exact window is quiet, Bloom sees fresh identifiers, and DGIM bits stay 0.', action: async () => { await runScenario('normal', true); switchTab('live'); }},
  {caption: '<b>Step 3 — card testing.</b> Repeated small failures on one card raise card velocity. The exact rule alone can reach block; Bloom and DGIM add corroborated signals, never proof.', action: async () => { await runScenario('card_testing', true); switchTab('live'); }},
  {caption: '<b>Step 4 — Bloom filter.</b> A fixed-size bit array answers “possibly seen before?”. Query before insert; first sightings are definitely absent. A false positive is possible, so the scoring rule requires corroboration. The rotating mode lets the filter forget.', action: async () => { await bloomRunDemo(); switchTab('bloom'); }},
  {caption: '<b>Step 5 — DGIM.</b> Estimate suspicious 1s in the last N stream elements with O(log N) buckets (O(log² N) bits in total). The worst-case stream shows the estimate tracking the exact count while bucket storage stays far below exact storage.', action: async () => { await dgimRunDemo('worst'); switchTab('dgim'); }},
  {caption: '<b>Step 6 — Apriori.</b> Run mixed traffic, choose Balanced, read the strongest pattern and check whether it holds up in the second half. Apriori only describes co-occurrence; it never scores an event.', action: async () => { switchTab('apriori'); await aprioriGuidedDemo(); }},
  {caption: '<b>Step 7 — Evidence.</b> Temporal split: first 70% by event time for tuning/training, last 30% held out. The seven-row ladder compares allow-all, the amount rule, exact rules, the full engine, LR-raw, LR-raw+BD, and LR-all on PR-AUC, recall@1%/5% FPR, and precision/recall/FPR with bootstrap intervals. Read the confidence intervals before ranking rows.', action: async () => { await refreshFull(); switchTab('evidence'); }},
  {caption: '<b>Wrap-up.</b> The model is deliberately transparent: proxy signals are documented, Bloom/DGIM are approximate, and labels are authored for teaching. Use the Evidence tab and CSV export when writing the report.', action: async () => switchTab('evidence')},
];

async function guidedGo(index) {
  guidedIndex = Math.max(0, Math.min(guidedSteps.length - 1, index));
  const step = guidedSteps[guidedIndex];
  $('guided-step').textContent = `${guidedIndex + 1} / ${guidedSteps.length}`;
  $('guided-caption').innerHTML = step.caption;
  $('guided-progress').style.width = `${((guidedIndex + 1) / guidedSteps.length) * 100}%`;
  $('guided-prev').disabled = guidedIndex === 0;
  $('guided-next').textContent = guidedIndex === guidedSteps.length - 1 ? 'Finish' : 'Next →';
  try { await step.action(); } catch (error) { toast(error.message); }
}

function openGuided() {
  guidedOpen = true;
  $('guided-overlay').classList.remove('hidden');
  guidedGo(0);
}
function closeGuided() {
  guidedOpen = false;
  $('guided-overlay').classList.add('hidden');
}

/* ---------- event wiring ---------- */
function wire() {
  document.querySelectorAll('.nav').forEach(button => button.onclick = () => switchTab(button.dataset.tab));
  $('play').onclick = async () => {
    if (!queue.length) return;
    playing = true; paused = false;
    const token = ++generation;
    updateControls();
    await pump(token);
  };
  $('pause').onclick = () => { paused = !paused; updateControls(); $('run-status').textContent = paused ? 'Paused' : 'Replaying'; };
  $('reset').onclick = resetSession;
  $('run-scenario').onclick = () => runScenario($('scenario').value, false);
  $('filter').onchange = renderEvents;
  $('shadow-view').onchange = () => { renderEvents(); renderInspection(); };
  $('events').addEventListener('click', ev => {
    const row = ev.target.closest('[data-id]');
    if (row) { selectedId = row.dataset.id; renderEvents(); renderInspection(); }
  });
  $('events').addEventListener('keydown', ev => {
    if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); ev.target.click(); }
  });
  document.addEventListener('click', ev => {
    const button = ev.target.closest('[data-entity-kind]');
    if (button) openEntity(button.dataset.entityKind, button.dataset.entityValue);
  });
  $('entity-close').onclick = () => $('entity-panel').classList.add('hidden');
  for (const k of ['review', 'block']) {
    $(`${k}-range`).oninput = () => { $(`${k}-value`).textContent = $(`${k}-range`).value; };
    $(`${k}-range`).onchange = async () => {
      let review = Number($('review-range').value), block = Number($('block-range').value);
      if (review >= block) { if (k === 'review') block = Math.min(100, review + 1); else review = Math.max(1, block - 1); }
      try { render(await api('thresholds', {review, block})); } catch (error) { toast(error.message); render(state); }
    };
  }
  $('custom').onsubmit = async ev => {
    ev.preventDefault();
    const form = new FormData(ev.target);
    const body = Object.fromEntries(form);
    body.amount = Number(body.amount); body.usage = Number(body.usage);
    for (const key of ['disposable', 'forwarding_email', 'country_mismatch', 'failed']) body[key] = form.has(key);
    try {
      const s = await api('event', body);
      selectedId = s.events?.[0]?.id || selectedId;
      render(s);
      $('run-status').textContent = 'Custom event evaluated · inspect the decision';
    } catch (error) { toast(error.message); }
  };
  $('sound').onclick = () => {
    soundOn = !soundOn;
    localStorage.setItem('radar-sound', soundOn ? 'on' : 'off');
    $('sound').textContent = soundOn ? 'Sound on' : 'Sound off';
    if (soundOn) beep('review');
  };
  $('theme').onclick = () => {
    const dark = !document.body.classList.contains('dark');
    document.body.classList.toggle('dark', dark);
    localStorage.setItem('radar-theme', dark ? 'dark' : 'light');
    $('theme').textContent = dark ? 'Light' : 'Dark';
  };
  $('guided').onclick = openGuided;
  $('guided-close').onclick = closeGuided;
  $('guided-next').onclick = () => guidedIndex >= guidedSteps.length - 1 ? closeGuided() : guidedGo(guidedIndex + 1);
  $('guided-prev').onclick = () => guidedGo(guidedIndex - 1);

  // Bloom
  const bloomCheck = async () => {
    const input = $('bloom-key');
    const key = input.value.trim();
    input.value = key;
    if (!key) { toast('Enter an identifier first.'); return; }
    try { render(await api('inspect', {kind: $('bloom-kind').value, key})); }
    catch (error) { toast(error.message); }
  };
  $('bloom-check').onclick = bloomCheck;
  $('bloom-key').onkeydown = event => {
    if (event.key === 'Enter') { event.preventDefault(); bloomCheck(); }
  };
  $('bloom-query-result').addEventListener('click', event => {
    const target = event.target.closest('[data-bloom-check]');
    if (!target) return;
    $('bloom-kind').value = target.dataset.bloomKind;
    $('bloom-key').value = target.dataset.bloomKey;
    bloomCheck();
  });
  $('bloom-run-demo').onclick = bloomRunDemo;

  // DGIM
  $('dgim-run').onclick = () => dgimRunDemo('random');
  $('dgim-worst').onclick = () => dgimRunDemo('worst');
  $('dgim-search').oninput = () => {
    dgimSearch = $('dgim-search').value;
    renderDgimEntityList(state?.dgim || {}, state?.dgim?.threshold ?? 5);
  };
  document.querySelectorAll('[data-dgim-filter]').forEach(button => {
    button.onclick = () => {
      dgimFilter = button.dataset.dgimFilter;
      renderDgimEntityList(state?.dgim || {}, state?.dgim?.threshold ?? 5);
      document.querySelectorAll('[data-dgim-filter]').forEach(item => {
        item.classList.toggle('active', item.dataset.dgimFilter === dgimFilter);
      });
    };
  });
  $('dgim-entity-list').addEventListener('click', ev => {
    const row = ev.target.closest('[data-dgim-kind]');
    if (row) loadDgimEntity(row.dataset.dgimKind, row.dataset.dgimName);
  });
  $('dgim-stream-cells').addEventListener('click', ev => {
    const cell = ev.target.closest('[data-dgim-event]');
    if (!cell) return;
    const id = cell.dataset.dgimEvent;
    if (!(state.events || []).some(item => item.id === id)) {
      toast('That event is outside the Live inspector\'s latest 150.');
      return;
    }
    selectedId = id;
    switchTab('live');
    renderEvents();
    renderInspection();
  });
  $('dgim-window-range').oninput = () => { $('dgim-window-value').textContent = $('dgim-window-range').value; };
  $('dgim-threshold-range').oninput = () => { $('dgim-threshold-value').textContent = $('dgim-threshold-range').value; };
  $('dgim-window-range').onchange = () => {
    const value = Number($('dgim-window-range').value);
    updateDgimConfig({dgim_window: value},
      `N changed to ${value} events; the verdict was re-evaluated.`);
  };
  $('dgim-threshold-range').onchange = () => {
    const value = Number($('dgim-threshold-range').value);
    updateDgimConfig({dgim_threshold: value},
      `Burst threshold changed to ${value}; the verdict was re-evaluated.`);
  };

  // Apriori
  $('apriori-run').onclick = aprioriGuidedDemo;
  $('apriori-support').oninput = () => { $('apriori-support-value').textContent = `${$('apriori-support').value}%`; };
  $('apriori-confidence').oninput = () => { $('apriori-confidence-value').textContent = `${$('apriori-confidence').value}%`; };
  const applyAdvanced = () => aprioriApply(
    Number($('apriori-support').value) / 100,
    Number($('apriori-confidence').value) / 100,
    'Custom thresholds applied.');
  $('apriori-support').onchange = applyAdvanced;
  $('apriori-confidence').onchange = applyAdvanced;
  $('apriori-suggest').onclick = aprioriUseSuggested;
  $('apriori-label-mode').onchange = renderApriori;
  document.querySelectorAll('input[name="apriori-preset"]').forEach(input => {
    input.onchange = () => { if (input.checked) aprioriApplyPreset(input.value); };
  });
  document.querySelectorAll('input[name="apriori-scope"]').forEach(input => {
    input.onchange = () => {
      if (!input.checked) return;
      aprioriScope = input.value;
      aprioriSelectionReset();
      renderApriori();
    };
  });
  $('apriori-show-all').onclick = () => {
    aprioriLimit = null;
    renderAprioriPatterns();
  };
  $('apriori-patterns').addEventListener('click', ev => {
    const item = ev.target.closest('[data-pattern-key]');
    if (!item) return;
    selectAprioriPattern(item.dataset.patternKey);
  });
  $('apriori-patterns').addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    const item = ev.target.closest('[data-pattern-key]');
    if (!item) return;
    ev.preventDefault();
    selectAprioriPattern(item.dataset.patternKey);
  });
  $('apriori-examples').addEventListener('click', ev => {
    if (!ev.target.closest('#apriori-example-close')) return;
    aprioriSelectedKey = null;
    renderAprioriPatterns();
    renderAprioriExamples();
  });
  $('apriori-status').addEventListener('click', ev => {
    if (!ev.target.closest('#apriori-retry')) return;
    const preset = aprioriView()?.presetForConfig(state?.apriori_config?.min_support, state?.apriori_config?.min_confidence);
    if (preset) aprioriApplyPreset(preset);
    else applyAdvanced();
  });
  const handleGlossaryTerm = ev => {
    const term = ev.target.closest('[data-glossary]');
    if (!term) return;
    ev.preventDefault();
    ev.stopPropagation();
    openAprioriGlossary(term.dataset.glossary);
  };
  $('tab-apriori').addEventListener('click', handleGlossaryTerm);
  $('tab-apriori').addEventListener('keydown', ev => {
    if (ev.key !== 'Enter' && ev.key !== ' ') return;
    if (ev.target.closest('[data-glossary]')) handleGlossaryTerm(ev);
  });

  // Keyboard: guided demo Space/R/Esc.
  document.addEventListener('keydown', ev => {
    if (!guidedOpen) return;
    if (ev.code === 'Space') { ev.preventDefault(); guidedIndex >= guidedSteps.length - 1 ? closeGuided() : guidedGo(guidedIndex + 1); }
    if (ev.key.toLowerCase() === 'r') { ev.preventDefault(); guidedGo(0); }
    if (ev.key === 'Escape') { ev.preventDefault(); closeGuided(); }
  });
}

async function openEntity(kind, value) {
  try {
    const data = await apiGet(`entity?kind=${encodeURIComponent(kind)}&value=${encodeURIComponent(value)}`);
    $('entity-title').textContent = `${kind}: ${value} · ${data.count} events`;
    $('entity-events').innerHTML = data.events.map(e => {
      const v = eventView(e);
      const reasons = (e.reasons || []).map(r => `+${r.points} ${esc(r.reason)}`).join(' · ') || 'no rules fired';
      return `<tr><td>${fmtTime(e.time - (data.dataset_start || 0))}</td><td>${esc(e.type)}</td><td>${num(v.score, 1)}</td><td><span class="badge ${esc(v.action)}">${esc(v.action)}</span></td><td class="hint">${reasons}</td></tr>`;
    }).join('');
    $('entity-panel').classList.remove('hidden');
  } catch (error) { toast(error.message); }
}

async function boot() {
  applyPrefs();
  wire();
  updateControls();
  await loadSynthetic();
}

boot();
