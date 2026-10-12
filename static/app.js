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
  const rows=state.events.filter(e=>$('filter').value==='all'||e.action===$('filter').value);
  $('empty').classList.toggle('hidden',rows.length>0);
  if (!rows.length && state.total) $('empty').innerHTML='<div>⌁</div><h3>No matching events</h3><p>Choose a different decision filter.</p>';
  else if(!state.total) $('empty').innerHTML='<div>⌁</div><h3>Your event stream starts here</h3><p>Run a scenario or send a custom event below.</p>';
  $('events').innerHTML=rows.map(e=>`<tr data-id="${e.id}" class="${selectedId===e.id?'selected':''}" tabindex="0" aria-label="Inspect ${esc(e.id)}"><td><strong>${esc(e.type.replace('_',' '))}</strong><small>${esc(e.account)}</small></td><td>${time(e.time)}</td><td><div class="score"><span class="mini-track"><i style="width:${e.score}%"></i></span>${e.score}</div></td><td><span class="badge ${e.action}">${e.action}</span></td></tr>`).join('');
}
function renderInspection(){
  const e=state.events.find(e=>e.id===selectedId);
  if(!e){$('inspection').innerHTML='<div class="empty"><div>◎</div><h3>Every score has a story</h3><p>Select an event to see its signals,<br>window counts and score breakdown.</p></div>';return;}
  $('inspection').innerHTML=`<div class="inspect-top"><div><strong>${e.score}<small> / 100</small></strong><p>Rule-based risk score · not probability</p></div><span class="badge ${e.action}">${e.action}</span></div>${e.reasons.length?e.reasons.map(r=>`<div class="reason"><b>+${r.points}</b><span>${esc(r.reason)}</span></div>`).join(''):'<p class="hint">No configured risk indicators were triggered.</p>'}<div class="feature-list"><span>Device accounts <b>${e.features.device_accounts}</b></span><span>IP accounts <b>${e.features.ip_accounts}</b></span><span>Card attempts <b>${e.features.card_attempts}</b></span><span>Device trials <b>${e.features.device_trials}</b></span></div><p class="hint">Counts at event time, within 60s. Score capped at 100.<br>Policy then: review ≥ ${e.review_threshold}, block ≥ ${e.block_threshold}.</p><div class="inspect-id">${esc(e.id)} · ${esc(e.device)}<br>Scenario truth: ${esc(e.label)} (not used in scoring)</div>`;
}
function renderAlgorithms(){
  $('window-summary').textContent=`Now ${time(state.clock)} · ${state.active} events in the active window · ${state.total} in session history.`;
  $('sketch-keys').textContent=`${state.sketch.keys} distinct devices`;
  $('sketch-table').innerHTML=state.sketch.comparisons.map(r=>`<tr><td>${esc(r.key)}</td><td>${r.exact}</td><td>${r.estimate}</td><td>+${r.estimate-r.exact}</td></tr>`).join('')||'<tr><td colspan="4">Run a scenario to populate the sketch.</td></tr>';
  $('patterns').innerHTML=state.patterns.map(p=>`<div class="pattern">${p.items.map(x=>esc(x.replaceAll('_',' '))).join(' + ')}<b>${percent(p.support)} support</b>${p.count} of ${state.total} events</div>`).join('')||'<p class="hint">Patterns appear after events with risk indicators arrive.</p>';
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
async function advance(){try{render(await api('advance',{seconds:61}));$('run-status').textContent='Advanced 61 simulated seconds · old window events expired';}catch(e){error(e);}}
$('advance').onclick=advance;$('expire').onclick=advance;
$('filter').onchange=renderTable;
$('events').addEventListener('click',e=>{const row=e.target.closest('[data-id]');if(row){selectedId=row.dataset.id;renderTable();renderInspection();}});
$('events').addEventListener('keydown',e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();e.target.click();}});
for(const k of ['review','block']){
  $(`${k}-range`).oninput=()=>{$(`${k}-value`).textContent=$(`${k}-range`).value;};
  $(`${k}-range`).onchange=async()=>{let review=Number($('review-range').value),block=Number($('block-range').value);if(review>=block){if(k==='review')block=review+1;else review=block-1;}try{render(await api('thresholds',{review,block}));}catch(e){error(e);render(state);}};
}
$('custom').onsubmit=async e=>{e.preventDefault();const form=new FormData(e.target),body=Object.fromEntries(form);body.amount=Number(body.amount);body.usage=Number(body.usage);for(const k of ['disposable','country_mismatch','failed'])body[k]=form.has(k);try{const s=await api('event',body);selectedId=s.events[0].id;render(s);$('run-status').textContent='Custom event evaluated · inspect the decision on the right';}catch(err){error(err);}};
document.querySelectorAll('.nav').forEach(button=>button.onclick=()=>{document.querySelectorAll('.nav').forEach(b=>b.classList.toggle('active',b===button));document.querySelectorAll('.page').forEach(p=>p.classList.toggle('hidden',p.id!==button.dataset.view));$('page-name').textContent=button.textContent.trim().slice(1).trim();window.scrollTo(0,0);});
api('state').then(render).catch(error);
