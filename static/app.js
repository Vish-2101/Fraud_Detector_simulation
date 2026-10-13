let state, selectedId = null, queue = [], paused = false, running = false, generation = 0;
const $ = id => document.getElementById(id);
const esc = v => String(v).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const time = v => `${String(Math.floor(v/60)).padStart(2,'0')}:${String(Math.floor(v%60)).padStart(2,'0')}`;
const percent = v => v === null ? '—' : `${(v*100).toFixed(1)}%`;
async function api(path, body) {
  const res = await fetch(`/api/${path}`, body === undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}
async function apiGet(path) {
  const res = await fetch(`/api/${path}`);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Request failed');
  return data;
}
function error(e) { $('toast').textContent = e.message; $('toast').classList.remove('hidden'); setTimeout(()=>$('toast').classList.add('hidden'),6000); }
function render(s) {
  state = s;
  ['total','active'].forEach(k => $(k).textContent = s[k]);
  $('clock').textContent=time(s.clock);
  $('allowed').textContent=s.counts.allow||0; $('reviewed').textContent=s.counts.review||0; $('blocked').textContent=s.counts.block||0;
  $('event-count').textContent=s.total;
  $('latency').textContent=`Local scoring: ${s.average_latency_ms.toFixed(3)} ms avg`;
  ['review','block'].forEach(k=>{$(`${k}-range`).value=s.thresholds[k];$(`${k}-value`).textContent=s.thresholds[k];});
  document.querySelector('.risk-scale').style.borderImage=`linear-gradient(90deg,#8bd3bf ${s.thresholds.review}%,#edcf91 ${s.thresholds.review}%,#edcf91 ${s.thresholds.block}%,#eca2b0 ${s.thresholds.block}%) 1`;
  ['precision','recall'].forEach(k=>$(k).textContent=percent(s.metrics[k]));
  ['tp','fp','tn','fn','labeled'].forEach(k=>$(k).textContent=s.metrics[k]);
  const max=Math.max(5,...s.series.map(b=>b.total));
  $('chart').innerHTML=s.series.length?s.series.map(b=>`<div class="chart-bar" title="${b.time}s: ${b.total} events, ${b.flagged} flagged"><div class="bar-total" style="height:${b.total/max*100}%"><div class="bar-flagged" style="height:${b.flagged/b.total*100}%"></div></div><span class="bar-label">${b.time}s</span></div>`).join(''):'<span class="hint" style="margin:auto">Activity will appear as events arrive</span>';
  if (!s.events.some(e=>e.id===selectedId)) selectedId=s.events[0]?.id||null;
  renderTable(); renderInspection(); renderAlgorithms();
}
function renderTable(){
  const shadowOn = $('shadow-toggle')?.checked;
  const rows=state.events.filter(e=>$('filter').value==='all'||e.action===$('filter').value);
  $('empty').classList.toggle('hidden',rows.length>0);
  if (!rows.length && state.total) $('empty').innerHTML='<div>⌁</div><h3>No matching events</h3><p>Choose a different decision filter.</p>';
  else if(!state.total) $('empty').innerHTML='<div>⌁</div><h3>Your event stream starts here</h3><p>Run a scenario or send a custom event below.</p>';
  $('events').innerHTML=rows.map(e=>{const sh=e.shadow||{};const tag=shadowOn&&sh.flipped?`<small>⚡ shadow: ${sh.base_score} ${sh.base_action}</small>`:'';return `<tr data-id="${e.id}" class="${selectedId===e.id?'selected':''}" tabindex="0" aria-label="Inspect ${esc(e.id)}"><td><strong>${esc(e.type.replace('_',' '))}</strong><small>${esc(e.account)}</small>${tag}</td><td>${time(e.time)}</td><td><div class="score"><span class="mini-track"><i style="width:${e.score}%"></i></span>${e.score}</div></td><td><span class="badge ${e.action}">${e.action}</span></td></tr>`;}).join('');
  const sh = state.shadow;
  if ($('shadow-summary') && sh) $('shadow-summary').textContent = sh.compared ? `shadow: ${sh.flipped} of ${sh.compared} decisions change without Bloom/DGIM (${sh.allow_to_flagged} allow→flagged)` : 'shadow: no events yet';
}
function renderInspection(){
  const e=state.events.find(e=>e.id===selectedId);
  if(!e){$('inspection').innerHTML='<div class="empty"><div>◎</div><h3>Every score has a story</h3><p>Select an event to see its signals,<br>window counts and score breakdown.</p></div>';return;}
  const b=e.bloom||{}, g=e.dgim||{}, sh=e.shadow||{};
  const shadowLine = sh.base_score!==undefined ? `<p class="hint">Shadow (exact rules only): <b>${sh.base_score} ${sh.base_action}</b> → full score <b>${e.score} ${e.action}</b>${sh.flipped?' · ⚡ Bloom/DGIM flipped this decision':''}.</p>` : '';
  $('inspection').innerHTML=`<div class="inspect-top"><div><strong>${e.score}<small> / 100</small></strong><p>Rule-based risk score · not probability</p></div><span class="badge ${e.action}">${e.action}</span></div>${e.reasons.length?e.reasons.map(r=>`<div class="reason"><b>+${r.points}</b><span>${esc(r.reason)}</span></div>`).join(''):'<p class="hint">No configured risk indicators were triggered.</p>'}<div class="feature-list"><span>Device accounts <b>${e.features.device_accounts}</b></span><span>IP accounts <b>${e.features.ip_accounts}</b></span><span>Card attempts <b>${e.features.card_attempts}</b></span><span>Device trials <b>${e.features.device_trials}</b></span><span>Bloom device <b>${esc(b.device_result||'—')}</b></span><span>Bloom card <b>${esc(b.card_result||'—')}</b></span><span>DGIM device est <b>${g.device_est??'—'}</b> (exact ${g.device_exact??'—'})</span><span>Bloom inserted <b>${b.inserted?'high-risk only: yes':'no (benign)'}</b></span></div>${shadowLine}<p class="hint">Counts at event time, within 60s. DGIM window is last ${g.window||60} stream elements (approx). Score capped at 100.<br>Policy then: review ≥ ${e.review_threshold}, block ≥ ${e.block_threshold}.</p><div class="inspect-id">${esc(e.id)} · ${esc(e.device)}<br>Scenario truth: ${esc(e.label)} (not used in scoring)</div>`;
}
function renderAlgorithms(){
  $('window-summary').textContent=`Now ${time(state.clock)} · ${state.active} events in the active 60s window · ${state.total} in session history.`;
  // Apriori
  const sup = Math.round((state.apriori_config?.min_support ?? 0.12)*100);
  if ($('support-value')) $('support-value').textContent = `${sup}%`;
  if ($('support-range') && document.activeElement !== $('support-range')) $('support-range').value = sup;
  const pats = state.patterns||[];
  $('patterns').innerHTML = pats.length ? pats.map(p=>`<div class="pattern">${p.items.map(x=>esc(x.replaceAll('_',' '))).join(' + ')}<b>${percent(p.support)} support</b>${p.count} of ${state.analyzed} events</div>`).join('') : `<p class="hint">No frequent itemsets yet at ${sup}% support — run card-testing or trial-abuse to accumulate repeated signals.</p>`;
  $('apriori-summary').textContent = `${state.analyzed||0} events analyzed · min support ${sup}% · showing up to 16 itemsets.`;
  const rules = state.rules||[];
  $('rules').innerHTML = rules.length ? `<h4 style="margin:10px 0 6px">Top association rules</h4>` + rules.map(r=>`<div class="pattern">${r.antecedent.map(x=>esc(x.replaceAll('_',' '))).join(' + ')} ⇒ ${r.consequent.map(x=>esc(x.replaceAll('_',' '))).join(' + ')}<b>conf ${(r.confidence*100).toFixed(1)}% · lift ${r.lift}</b>sup ${percent(r.support)}</div>`).join('') : '';
  // Evaluation-only label rules (offline; labels never scored)
  const ev = state.apriori_eval;
  if ($('eval-rules')) $('eval-rules').innerHTML = ev?.rules?.length ? ev.rules.map(r=>`<div class="pattern">${r.antecedent.map(x=>esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')} ⇒ ${r.consequent.map(x=>esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')}<b>conf ${(r.confidence*100).toFixed(1)}% · lift ${r.lift}</b>sup ${percent(r.support)}</div>`).join('') : '<p class="hint">No label rules yet — run Mixed traffic so abuse/legitimate baskets accumulate.</p>';
  const ts = state.time_split;
  if ($('time-split')) $('time-split').innerHTML = ts?.checked?.length ? `<p class="hint">Train on first ${ts.train_n}, test on last ${ts.test_n}.</p>` + ts.checked.map(c=>`<div class="pattern">${c.antecedent.map(x=>esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')} ⇒ ${c.consequent.map(x=>esc(x.replaceAll('_',' ').replace('label:',''))).join(' + ')}<b>train ${(c.train_confidence*100).toFixed(1)}% → test ${c.test_confidence===null?'—':(c.test_confidence*100).toFixed(1)+'%'} · lift ${c.lift}</b></div>`).join('') : `<p class="hint">${esc(ts?.note||'Need at least 4 events for a time split.')}</p>`;
  // DGIM
  const d = state.dgim||{};
  if ($('dgim-window')) $('dgim-window').textContent = d.window_size ?? state.dgim_config?.window ?? 60;
  if ($('dgim-value')) $('dgim-value').textContent = state.dgim_config?.threshold ?? 5;
  const sel = $('dgim-entity');
  if (sel && d.entities) {
    const cur = sel.value || d.entity;
    sel.innerHTML = (d.entities.length?d.entities:[d.entity].filter(Boolean)).map(k=>`<option ${k===cur?'selected':''}>${esc(k)}</option>`).join('') || '<option value="">—</option>';
  }
  if ($('dgim-summary')) $('dgim-summary').innerHTML = d.entity ? `<p>Entity <b>${esc(d.entity)}</b> · stream pos ${d.stream_pos} · approximate <b>${d.estimate}</b> vs exact <b>${d.exact}</b> suspicious 1s in last ${d.window_size} elements.</p>` : '<p class="hint">Run a scenario to populate DGIM streams.</p>';
  if ($('dgim-table')) $('dgim-table').innerHTML = (d.buckets||[]).map(b=>`<tr><td>${b.pos}</td><td>${b.size}</td></tr>`).join('') || '<tr><td colspan="2">No buckets yet (no suspicious 1s).</td></tr>';
  // Bloom
  const bd = state.bloom?.device, bc = state.bloom?.card;
  if ($('bloom-summary') && bd && bc) $('bloom-summary').textContent = `Device filter: ${bd.bits_set}/${bd.size} bits (${(bd.occupancy*100).toFixed(1)}%), ${bd.insertions} inserts, FPP ${ (bd.theoretical_fpp*100).toFixed(2)}% · Card filter: ${bc.bits_set}/${bc.size} bits, FPP ${(bc.theoretical_fpp*100).toFixed(2)}%`;
  if ($('bloom-grids') && bd) {
    const grid = bits => `<div style="display:grid;grid-template-columns:repeat(32,1fr);gap:1px;max-width:420px;margin:6px 0">${bits.map(v=>`<span style="width:10px;height:10px;background:${v?'#6554d9':'#e9eaf2'};display:inline-block" title="${v?'set':'unset'}"></span>`).join('')}</div>`;
    $('bloom-grids').innerHTML = `<p class="hint">Device bits (first ${bd.bits.length} shown${bd.bits_truncated?' — truncated':''})</p>${grid(bd.bits)}<p class="hint">Card bits</p>${grid(bc.bits)}`;
  }
  if (state.inspection && $('bloom-result')) $('bloom-result').textContent = `${state.inspection.kind}:${state.inspection.key} → ${state.inspection.result} (query counted, no insert)`;
}
async function runDgimDemo(){
  try{
    $('dgim-demo-out').innerHTML='<p class="hint">Running 1000-element stream…</p>';
    const d = await apiGet('dgim_demo?n=1000');
    const s = d.summary;
    const maxV = Math.max(...d.samples.map(p=>Math.max(p.exact,p.estimate,1)));
    const bars = d.samples.map(p=>`<div class="chart-bar" title="pos ${p.pos}: exact ${p.exact}, est ${p.estimate}, err ${(p.rel_error*100).toFixed(1)}%, ${p.buckets} buckets"><div class="bar-total" style="height:${p.exact/maxV*100}%"><div class="bar-flagged" style="height:${p.estimate/Math.max(1,p.exact)*100}%"></div></div><span class="bar-label">${p.pos}</span></div>`).join('');
    $('dgim-demo-out').innerHTML=`<p>1000 bits, window ${s.window}: max ${s.max_buckets} buckets vs ${s.exact_storage}-bit exact storage · max rel error ${(s.max_rel_error*100).toFixed(1)}%, avg ${(s.avg_rel_error*100).toFixed(1)}%. Purple = exact, red overlay = estimate.</p><div class="chart" style="height:120px">${bars}</div><p class="hint">${esc(s.note)}</p>`;
  }catch(e){error(e);}
}
function controls(){ $('run').disabled=running; $('pause').disabled=!running; $('pause').textContent=paused?'Resume':'Pause'; }
async function play(token){
  let failed=false;
  while(queue.length && token===generation){
    if(paused){await new Promise(r=>setTimeout(r,100));continue;}
    try{const s=await api('event',queue.shift()); if(token!==generation)return; selectedId=s.events[0]?.id;render(s);}
    catch(e){error(e);queue=[];failed=true;break;}
    $('run-status').textContent=`Simulation running · ${queue.length} events remaining`;
    await new Promise(r=>setTimeout(r,Number($('speed').value)));
  }
  if(token===generation){running=false;paused=false;controls();$('run-status').textContent=failed?'Simulation stopped · see the error message':'Simulation complete · select an event to inspect the evidence';}
}
$('run').onclick=async()=>{running=true;paused=false;controls();const token=++generation;try{const result=await api('scenario',{name:$('scenario').value});if(token!==generation)return;queue=result.events;await play(token);}catch(e){if(token===generation){running=false;controls();error(e);}}};
$('pause').onclick=()=>{paused=!paused;controls();$('run-status').textContent=paused?'Simulation paused':'Simulation resumed';};
$('reset').onclick=async()=>{generation++;queue=[];paused=false;running=false;controls();try{selectedId=null;render(await api('reset',{}));$('run-status').textContent='Session reset · ready for a new demonstration';}catch(e){error(e);}};
async function advance(){try{render(await api('advance',{seconds:61}));$('run-status').textContent='Advanced 61 simulated seconds · exact window expired (Bloom persists; DGIM is count-based)';}catch(e){error(e);}}
$('advance').onclick=advance;if($('expire'))$('expire').onclick=advance;
$('filter').onchange=renderTable;
$('events').addEventListener('click',e=>{const row=e.target.closest('[data-id]');if(row){selectedId=row.dataset.id;renderTable();renderInspection();}});
$('events').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.target.click();}});
for(const k of ['review','block']){
  $(`${k}-range`).oninput=()=>{$(`${k}-value`).textContent=$(`${k}-range`).value;};
  $(`${k}-range`).onchange=async()=>{let review=Number($('review-range').value),block=Number($('block-range').value);if(review>=block){if(k==='review')block=review+1;else review=block-1;}try{render(await api('thresholds',{review,block}));}catch(e){error(e);render(state);}};
}
if($('support-range')){$('support-range').onchange=async()=>{try{render(await api('config',{min_support:Number($('support-range').value)/100}));}catch(e){error(e);}};}
if($('dgim-range')){$('dgim-range').onchange=async()=>{try{render(await api('config',{dgim_threshold:Number($('dgim-range').value)}));}catch(e){error(e);}};}
if($('dgim-entity')){$('dgim-entity').onchange=async()=>{try{render(await apiGet(`state?entity=${encodeURIComponent($('dgim-entity').value)}`));}catch(e){error(e);}};}
if($('bloom-check')){$('bloom-check').onclick=async()=>{try{render(await api('inspect',{kind:$('bloom-kind').value,key:$('bloom-key').value}));}catch(e){error(e);}};}
if($('dgim-demo')){$('dgim-demo').onclick=runDgimDemo;}
if($('shadow-toggle')){$('shadow-toggle').onchange=()=>renderTable();}
$('custom').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.target),body=Object.fromEntries(form);body.amount=Number(body.amount);body.usage=Number(body.usage);for(const k of ['disposable','country_mismatch','failed'])body[k]=form.has(k);try{const s=await api('event',body);selectedId=s.events[0].id;render(s);$('run-status').textContent='Custom event evaluated · inspect the decision on the right';}catch(err){error(err);}};
document.querySelectorAll('.nav').forEach(button=>button.onclick=()=>{document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b===button));document.querySelectorAll('.page').forEach(p=>p.classList.toggle('hidden',p.id!==button.dataset.view));$('page-name').textContent=button.textContent.trim().slice(1).trim();window.scrollTo(0,0);});
apiGet('state').then(render).catch(error);
