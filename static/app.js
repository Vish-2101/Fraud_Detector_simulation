/* Radar Lab UI. No third-party JS; SVG/CSS only. */
let state = null;
let selectedId = null;
let queue = [];
let demoEvents = [];
let playing = false;
let paused = false;
let generation = 0;
let aprioriItemsets = [];
let aprioriSelected = null;
let guidedIndex = 0;
let guidedOpen = false;
let lastTotal = 0;
let lastBeep = 0;
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
  $('bloom-summary').textContent = `Device: m=${bd.m || bd.size}, k=${bd.k || bd.num_hashes}, ${bd.insertions} inserts, ${bd.bits_set} bits set. Card: ${bc.insertions} inserts, FPP ${pct(bc.theoretical_fpp)}.`;
  $('bloom-mode').textContent = bd.rotating ? `rotating every ${bd.rotate_every}` : 'static';
  $('bloom-theory').textContent = pct(bd.theoretical_fpp);
  $('bloom-measured').textContent = bd.measured_fpp === null ? '—' : pct(bd.measured_fpp);
  $('bloom-occupancy').textContent = pct(bd.occupancy);
  const inspection = state.inspection;
  const kind = inspection?.kind;
  const positions = inspection ? inspection.positions : [];
  renderBitGrid($('bloom-device-grid'), bd.bits || [], kind === 'device' ? positions : []);
  renderBitGrid($('bloom-card-grid'), bc.bits || [], kind === 'card' ? positions : []);
  if (inspection) {
    $('bloom-query-result').textContent = `${inspection.kind}:${inspection.key} → ${inspection.result.replace('_', ' ')} · positions [${(inspection.positions || []).join(', ')}] (query counted, no insert)`;
  }
  const mEl = $('bloom-m'), kEl = $('bloom-k'), nEl = $('bloom-n');
  if (mEl && document.activeElement !== mEl) mEl.value = bd.m || bd.size;
  if (kEl && document.activeElement !== kEl) kEl.value = bd.k || bd.num_hashes;
  if (mEl) $('bloom-m-value').textContent = mEl.value;
  if (kEl) $('bloom-k-value').textContent = kEl.value;
  if (nEl) $('bloom-n-value').textContent = nEl.value;
  updateBloomSizing();
}

function renderBitGrid(container, bits, positions) {
  if (!container) return;
  const shown = bits.slice(0, 512);
  const pos = new Set((positions || []).filter(p => p < shown.length));
  container.innerHTML = shown.map((bit, i) => {
    const cls = bit && pos.has(i) ? 'both' : pos.has(i) ? 'query' : bit ? 'set' : '';
    return `<span class="${cls}" title="bit ${i}: ${bit ? 'set' : 'unset'}${pos.has(i) ? ' · current query' : ''}"></span>`;
  }).join('');
  const next = container.nextElementSibling;
  if (next?.classList.contains('bit-grid-note')) next.remove();
  if (bits.length > shown.length) {
    container.insertAdjacentHTML('afterend', `<p class="hint bit-grid-note">Showing first ${shown.length} of ${bits.length} bits.</p>`);
  }
}

function bloomCapacity(n, fpp = 0.01) {
  const m = Math.max(16, Math.ceil(-n * Math.log(fpp) / (Math.log(2) ** 2)));
  const k = Math.max(1, Math.min(10, Math.round((m / n) * Math.log(2))));
  return {m, k};
}
function updateBloomSizing() {
  const n = Number($('bloom-n')?.value || 100);
  const {m, k} = bloomCapacity(n, 0.01);
  $('bloom-sizing').textContent = `For n=${n} at 1% target FPP: m=${m}, k=${k}. Apply to reset the session filters.`;
}

function renderDgim() {
  const d = state.dgim || {};
  const windowEl = $('dgim-window-range');
  const thresholdEl = $('dgim-threshold-range');
  if (windowEl && document.activeElement !== windowEl) windowEl.value = d.window_size || state.dgim_config?.window || 60;
  if (thresholdEl && document.activeElement !== thresholdEl) thresholdEl.value = state.dgim_config?.threshold || 5;
  $('dgim-window-value').textContent = d.window_size || state.dgim_config?.window || 60;
  $('dgim-threshold-value').textContent = state.dgim_config?.threshold || 5;
  const select = $('dgim-entity');
  if (select) {
    const entities = d.entities || (d.entity ? [d.entity] : []);
    const current = select.value || d.entity || '';
    select.innerHTML = entities.length ? entities.map(k => `<option ${k === current ? 'selected' : ''}>${esc(k)}</option>`).join('') : '<option value="">—</option>';
  }
  $('dgim-entity-chip').textContent = d.entity || 'no entity';
  $('dgim-summary').innerHTML = d.entity
    ? `Entity <b>${esc(d.entity)}</b> · stream position ${d.stream_pos} · approximate <b>${d.estimate}</b> vs exact <b>${d.exact}</b> suspicious 1s in the last ${d.window_size} elements · ${d.bucket_count} buckets.`
    : 'Run a scenario to populate per-device DGIM streams.';
  const buckets = d.buckets || [];
  const colors = ['#6554d9','#3b7dd8','#229578','#bc8837','#de6378','#8e5cc7','#2f9bb3'];
  $('dgim-buckets').innerHTML = buckets.length ? buckets.map((b, i) => {
    const width = Math.min(90, 18 + Math.log2(b.size + 1) * 16);
    return `<div class="bucket" style="width:${width}px;height:${Math.min(62, 18 + Math.log2(b.size + 1) * 10)}px;background:${colors[i % colors.length]}" title="position ${b.pos}, size ${b.size}">${b.size}</div>`;
  }).join('') : '<span class="hint">No buckets yet (no suspicious 1s).</span>';
}

function renderApriori() {
  if (!state.apriori_config) return;
  const support = Math.round(state.apriori_config.min_support * 100);
  const confidence = Math.round((state.apriori_config.min_confidence || 0) * 100);
  const supportEl = $('apriori-support'), confidenceEl = $('apriori-confidence');
  if (supportEl && document.activeElement !== supportEl) supportEl.value = support;
  if (confidenceEl && document.activeElement !== confidenceEl) confidenceEl.value = confidence;
  $('apriori-support-value').textContent = `${support}%`;
  $('apriori-confidence-value').textContent = `${confidence}%`;
  const levels = state.apriori_levels || [];
  $('apriori-levels').innerHTML = levels.length ? `<div class="levels">${levels.map(l => `<div class="level">L${l.level}<b>${l.frequent}</b>${l.candidates} candidates → ${l.frequent} frequent</div>`).join('')}</div>` : '<p class="hint">Run a scenario for level-wise counts.</p>';
  aprioriItemsets = state.patterns || [];
  $('apriori-itemsets').innerHTML = aprioriItemsets.length ? aprioriItemsets.map((p, i) => `<div class="pattern clickable" data-itemset-index="${i}">${p.items.map(x => esc(x.replaceAll('_', ' '))).join(' + ')}<b>${pct(p.support)} support · ${p.count} of ${state.analyzed}</b></div>`).join('') : `<p class="hint">No frequent itemsets yet at ${support}% support. Try the suggested support or run card-testing/trial-abuse.</p>`;
  const rules = state.rules || [];
  $('apriori-rules').innerHTML = rules.length ? rules.map(r => `<div class="pattern">${r.antecedent.map(x => esc(x.replaceAll('_', ' '))).join(' + ')} ⇒ ${r.consequent.map(x => esc(x.replaceAll('_', ' '))).join(' + ')}<b>conf ${pct(r.confidence)} · lift ${num(r.lift, 2)} · support ${pct(r.support)}</b></div>`).join('') : '<p class="hint">No rules above the confidence threshold.</p>';
  renderAprioriBaskets();
  const cls = state.apriori_class;
  $('apriori-class').innerHTML = cls ? `
    <p class="hint">Prevalence <b>${pct(cls.prevalence)}</b> · suggested support <b>${pct(cls.suggested_min_support)}</b> · using ${pct(cls.min_support)}. ${esc(cls.note)}</p>
    <div class="two-col">
      <div><h4>All baskets (${cls.all_events.analyzed})</h4>${(cls.all_events.itemsets || []).slice(0, 6).map(p => `<div class="pattern">${p.items.map(x => esc(x.replaceAll('_',' '))).join(' + ')}<b>${pct(p.support)}</b></div>`).join('') || '<p class="hint">None.</p>'}</div>
      <div><h4>Fraud-only subset (${cls.abuse_only.analyzed})</h4>${(cls.abuse_only.itemsets || []).slice(0, 6).map(p => `<div class="pattern">${p.items.map(x => esc(x.replaceAll('_',' '))).join(' + ')}<b>${pct(p.support)}</b></div>`).join('') || '<p class="hint">No labeled abuse baskets.</p>'}</div>
    </div>` : '<p class="hint">Full state not loaded yet.</p>';
  const ts = state.time_split;
  $('apriori-split').innerHTML = ts?.checked?.length
    ? `<p class="hint">Mine first ${ts.train_n}, test on last ${ts.test_n}.</p>${ts.checked.map(c => `<div class="pattern">${c.antecedent.map(x => esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')} ⇒ ${c.consequent.map(x => esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')}<b>train ${pct(c.train_confidence)} → test ${c.test_confidence === null ? '—' : pct(c.test_confidence)} · lift ${num(c.lift, 2)}</b></div>`).join('')}`
    : `<p class="hint">${esc(ts?.note || 'Need at least 4 events for a time split.')}</p>`;
  const ev = state.apriori_eval;
  $('apriori-eval').innerHTML = ev?.rules?.length
    ? `<p class="warning">Offline mode only. Labels are added as basket items here; this is not scoring.</p>${ev.rules.map(r => `<div class="pattern">${r.antecedent.map(x => esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')} ⇒ ${r.consequent.map(x => esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')}<b>conf ${pct(r.confidence)} · lift ${num(r.lift, 2)}</b></div>`).join('')}`
    : '<p class="hint">No label rules yet. Run Mixed traffic.</p>';
  $('apriori-eval').classList.toggle('hidden', !$('apriori-label-mode')?.checked);
}

function renderAprioriBaskets() {
  if (!aprioriSelected) {
    $('apriori-baskets').innerHTML = '<span class="hint">Select an itemset.</span>';
    return;
  }
  const wanted = new Set(aprioriSelected.items);
  const matches = (state.events || []).filter(e => [...wanted].every(item => (e.items || []).includes(item)));
  $('apriori-baskets').innerHTML = matches.length
    ? `<p class="hint">${matches.length} matching baskets in the latest ${state.events.length} events (full session may be larger).</p>` + matches.slice(0, 12).map(e => `<div class="pattern">${esc(e.id)} · ${esc(e.type)} · ${esc(e.account)}<b>${(e.items || []).map(x => esc(x.replaceAll('_', ' '))).join(', ')}</b></div>`).join('')
    : '<span class="hint">No matching baskets in the currently loaded window.</span>';
}

function renderEvidence() {
  const ev = state?.evaluation;
  $('baseline-source').textContent = baselineSource();
  if (!ev) {
    $('evidence-split').textContent = 'Replay the synthetic demo to compute the comparison.';
    $('evidence-threshold').textContent = 'review ≥ —';
    $('evidence-compare').innerHTML = '<tr><td colspan="3" class="hint">Playback state only. Open this tab after replaying the synthetic demo to compute full evidence.</td></tr>';
    $('prauc-full').textContent = 'PR-AUC —';
    $('prauc-baseline').textContent = 'PR-AUC —';
    $('pr-chart-full').innerHTML = '';
    $('pr-chart-baseline').innerHTML = '';
    return;
  }
  const full = ev.views?.full || {available: false};
  const baseline = ev.views?.baseline || {available: false};
  const value = (v, read, fmt = x => x) => {
    if (!v.available) return '—';
    const x = read(v);
    return x === undefined || x === null ? '—' : fmt(x);
  };
  const count = (v, key) => v.available ? (v.confusion?.[key] ?? 0) : '—';
  const recall = (v, key) => value(v, x => x[key]?.recall, pct);
  const rows = [
    ['True positives', count(full, 'tp'), count(baseline, 'tp')],
    ['False positives', count(full, 'fp'), count(baseline, 'fp')],
    ['False negatives', count(full, 'fn'), count(baseline, 'fn')],
    ['True negatives', count(full, 'tn'), count(baseline, 'tn')],
    ['Precision', value(full, v => v.precision, pct), value(baseline, v => v.precision, pct)],
    ['Recall', value(full, v => v.recall, pct), value(baseline, v => v.recall, pct)],
    ['False-positive rate', value(full, v => v.false_positive_rate, pct), value(baseline, v => v.false_positive_rate, pct)],
    ['PR-AUC', value(full, v => v.pr_auc, x => num(x, 3)), value(baseline, v => v.pr_auc, x => num(x, 3))],
    ['Recall @ 1% FPR', recall(full, 'recall_at_1pct_fpr'), recall(baseline, 'recall_at_1pct_fpr')],
    ['Recall @ 5% FPR', recall(full, 'recall_at_5pct_fpr'), recall(baseline, 'recall_at_5pct_fpr')],
    ['Labeled test events', value(full, v => v.labeled), value(baseline, v => v.labeled)],
    ['Positive test events', value(full, v => v.positives), value(baseline, v => v.positives)],
  ];
  $('evidence-split').textContent = `Test split: last ${ev.split.test_n} of ${ev.split.train_n + ev.split.test_n} labeled events · ${ev.method}`;
  $('evidence-threshold').textContent = `review ≥ ${ev.thresholds.review}`;
  $('evidence-compare').innerHTML = rows.map(([metric, f, b]) => `<tr><td>${metric}</td><td>${f}</td><td>${b}</td></tr>`).join('');
  $('prauc-full').textContent = full.available ? `PR-AUC ${num(full.pr_auc, 3)}` : 'PR-AUC —';
  $('prauc-baseline').textContent = baseline.available ? `PR-AUC ${num(baseline.pr_auc, 3)}` : 'PR-AUC —';
  $('pr-chart-full').innerHTML = full.available ? svgPrChart(full.pr_curve || []) : '<p class="hint">Bloom/DGIM view unavailable.</p>';
  $('pr-chart-baseline').innerHTML = baseline.available ? svgPrChart(baseline.pr_curve || []) : '<p class="hint">Learned baseline unavailable.</p>';
  const ablation = ev.ablation || [];
  const maxFlip = Math.max(1, ...ablation.map(a => a.flips));
  $('ablation').innerHTML = ablation.length ? ablation.map(a => `<div class="pattern"><div style="display:flex;justify-content:space-between;gap:10px"><span>${esc(a.rule)}</span><b style="margin:0">${a.flips} flip${a.flips === 1 ? '' : 's'} / ${a.events} events</b></div><div style="height:7px;background:var(--line);border-radius:5px;margin-top:5px;overflow:hidden"><i style="display:block;height:100%;width:${a.flips / maxFlip * 100}%;background:var(--red)"></i></div></div>`).join('') : '<p class="hint">No rule-ablation data on the test split.</p>';
}

function baselineSource() {
  const t = state?.baseline_training;
  if (!t) return 'Baseline source: the running app loads models/baseline.json.';
  return `Baseline source: the running app loads models/baseline.json. That committed model was trained offline by tools/train_baseline.py on the ${t.dataset} fixture: ${t.train_events} training events (${t.train_positives} positives), ${t.test_events} held-out events, using a temporal split — ${t.temporal_split}.`;
}

function svgPrChart(points) {
  if (!points.length) return '<p class="hint">No positive test events, so the PR curve is undefined.</p>';
  const w = 520, h = 200, pad = 30;
  const x = r => pad + r * (w - pad * 1.4);
  const y = p => h - pad - p * (h - pad * 1.6);
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.recall).toFixed(1)},${y(p.precision).toFixed(1)}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Precision recall curve">
    <line x1="${pad}" y1="${h - pad}" x2="${w - 10}" y2="${h - pad}" stroke="var(--line)"/>
    <line x1="${pad}" y1="${pad / 2}" x2="${pad}" y2="${h - pad}" stroke="var(--line)"/>
    <text x="${w / 2}" y="${h - 6}" font-size="9" fill="var(--muted)" text-anchor="middle">recall</text>
    <text x="8" y="16" font-size="9" fill="var(--muted)">precision</text>
    <path d="${path}" fill="none" stroke="var(--purple)" stroke-width="2.2"/>
    <circle cx="${x(points[0].recall)}" cy="${y(points[0].precision)}" r="3" fill="var(--amber)"/>
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
    await api('bloom/config', {size: 128, num_hashes: 3, rotate_every: 0});
    const s = await api('scenario_run', {name: 'card_testing'});
    render(s);
    switchTab('bloom');
    toast('Bloom demo: card-testing burst inserted high-risk devices/cards. Query a fresh identifier or find a false positive.');
  } catch (error) { toast(error.message); }
}

async function aprioriRunDemo() {
  try {
    const s = await api('scenario_run', {name: 'mixed'});
    render(s);
    const suggested = s.apriori_config?.suggested_min_support ?? 0.05;
    const next = await api('apriori', {min_support: suggested, min_confidence: 0});
    render(next);
    aprioriSelected = null;
    switchTab('apriori');
    toast(`Apriori demo: mined mixed traffic at the auto-suggested support (${(suggested * 100).toFixed(1)}%).`);
  } catch (error) { toast(error.message); }
}

async function dgimRunDemo(pattern = 'random') {
  try {
    $('dgim-chart').innerHTML = '<p class="hint">Running 1000-bit stream…</p>';
    const data = await apiGet(`dgim_demo?n=1000&window=100&pattern=${encodeURIComponent(pattern)}`);
    const summary = data.summary;
    $('dgim-chart').innerHTML = svgLineChart(data.samples);
    $('dgim-storage-chip').textContent = `${summary.max_buckets} buckets vs ${summary.exact_storage} exact`;
    $('dgim-demo-summary').innerHTML = `1000 bits, window ${summary.window} (${esc(summary.pattern)}): max ${summary.max_buckets} buckets vs ${summary.exact_storage}-bit exact storage · max relative error ${pct(summary.max_rel_error)}, average ${pct(summary.avg_rel_error)}. ${esc(summary.error_bound)}.`;
    switchTab('dgim');
  } catch (error) { toast(error.message); }
}

async function findFalsePositive() {
  try {
    const size = Number($('bloom-m').value || 64);
    const k = Number($('bloom-k').value || 3);
    const data = await api('bloom/find_false_positive', {size, num_hashes: k});
    if (!data.found) {
      $('bloom-fp-result').innerHTML = `<p class="hint">No false positive found after ${data.insertions} inserts at m=${size}, k=${k}. Increase inserts or shrink the filter.</p>`;
      return;
    }
    const colliding = data.collisions.map(c => `<div class="pattern">${esc(c.key)}<b>positions [${c.positions.join(', ')}]</b></div>`).join('');
    $('bloom-fp-result').innerHTML = `<div class="pattern"><b style="margin:0">False positive found after ${data.insertions} inserts</b>Unseen key <code>${esc(data.probe)}</code> maps to bits [${data.probe_positions.join(', ')}], all already set.<br>Filter occupancy ${pct(data.bits_set / size)} · theoretical FPP ${pct(data.theoretical_fpp)}.</div>${colliding}`;
  } catch (error) { toast(error.message); }
}

/* ---------- navigation and preferences ---------- */
async function switchTab(name) {
  document.querySelectorAll('.nav').forEach(b => b.classList.toggle('active', b.dataset.tab === name));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('hidden', p.id !== `tab-${name}`));
  if (name !== 'live') {
    if (!state?.evaluation || !state?.apriori_levels) await refreshFull();
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
  {caption: '<b>Step 5 — DGIM.</b> Estimate suspicious 1s in the last N stream elements with O(log²N) buckets. The worst-case stream shows the estimate tracking the exact count while bucket storage stays far below exact storage.', action: async () => { await dgimRunDemo('worst'); switchTab('dgim'); }},
  {caption: '<b>Step 6 — Apriori.</b> Mine co-occurring signals offline. Fixed 12% support hides fraud patterns at 3.5% prevalence, so use the auto-suggested support and compare all baskets with the fraud-only subset. Co-occurrence is not causation.', action: async () => { await aprioriRunDemo(); switchTab('apriori'); }},
  {caption: '<b>Step 7 — Evidence.</b> Temporal split: first 70% by event time for tuning/training, last 30% held out. Compare Bloom/DGIM and the learned baseline side by side on confusion counts, precision, recall, FPR, PR-AUC and recall@1%/5% FPR, plus per-rule ablations. Report inactive rules honestly.', action: async () => { await refreshFull(); switchTab('evidence'); }},
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
  ['bloom-m', 'bloom-k', 'bloom-n'].forEach(id => $(id).oninput = () => {
    if (id === 'bloom-m') $('bloom-m-value').textContent = $(id).value;
    if (id === 'bloom-k') $('bloom-k-value').textContent = $(id).value;
    if (id === 'bloom-n') $('bloom-n-value').textContent = $(id).value;
    updateBloomSizing();
  });
  $('bloom-apply').onclick = async () => {
    try {
      render(await api('bloom/config', {size: Number($('bloom-m').value), num_hashes: Number($('bloom-k').value), rotate_every: 0}));
      await refreshFull();
    } catch (error) { toast(error.message); }
  };
  $('bloom-check').onclick = async () => {
    try { render(await api('inspect', {kind: $('bloom-kind').value, key: $('bloom-key').value})); }
    catch (error) { toast(error.message); }
  };
  $('bloom-find-fp').onclick = findFalsePositive;
  $('bloom-run-demo').onclick = bloomRunDemo;

  // DGIM
  $('dgim-run').onclick = () => dgimRunDemo('random');
  $('dgim-worst').onclick = () => dgimRunDemo('worst');
  $('dgim-scale').onclick = () => dgimRunDemo('random');
  $('dgim-window-range').onchange = async () => {
    try { render(await api('config', {dgim_window: Number($('dgim-window-range').value)})); }
    catch (error) { toast(error.message); }
  };
  $('dgim-threshold-range').onchange = async () => {
    try { render(await api('config', {dgim_threshold: Number($('dgim-threshold-range').value)})); }
    catch (error) { toast(error.message); }
  };
  $('dgim-entity').onchange = async () => {
    try { render(await apiGet(`state?entity=${encodeURIComponent($('dgim-entity').value)}`)); }
    catch (error) { toast(error.message); }
  };

  // Apriori
  $('apriori-support').oninput = () => { $('apriori-support-value').textContent = `${$('apriori-support').value}%`; };
  $('apriori-confidence').oninput = () => { $('apriori-confidence-value').textContent = `${$('apriori-confidence').value}%`; };
  $('apriori-support').onchange = async () => {
    try { render(await api('apriori', {min_support: Number($('apriori-support').value) / 100, min_confidence: Number($('apriori-confidence').value) / 100})); }
    catch (error) { toast(error.message); }
  };
  $('apriori-confidence').onchange = async () => {
    try { render(await api('apriori', {min_support: Number($('apriori-support').value) / 100, min_confidence: Number($('apriori-confidence').value) / 100})); }
    catch (error) { toast(error.message); }
  };
  $('apriori-suggest').onclick = async () => {
    const suggested = state?.apriori_config?.suggested_min_support ?? 0.05;
    $('apriori-support').value = Math.max(1, Math.round(suggested * 100));
    $('apriori-support-value').textContent = `${$('apriori-support').value}%`;
    try { render(await api('apriori', {min_support: suggested, min_confidence: Number($('apriori-confidence').value) / 100})); }
    catch (error) { toast(error.message); }
  };
  $('apriori-run').onclick = aprioriRunDemo;
  $('apriori-label-mode').onchange = () => $('apriori-eval').classList.toggle('hidden', !$('apriori-label-mode').checked);
  $('apriori-itemsets').addEventListener('click', ev => {
    const item = ev.target.closest('[data-itemset-index]');
    if (!item) return;
    aprioriSelected = aprioriItemsets[Number(item.dataset.itemsetIndex)] || null;
    renderAprioriBaskets();
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
  updateBloomSizing();
  updateControls();
  await loadSynthetic();
}

boot();
