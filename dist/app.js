import { dayKey, eventStatus, filterEvents, formatDate, friendlyDay, entryLabel, signupLabel, calendarFile, demoEvents, safeUrl, isValidEvent } from './events.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const paths = {
  bookmark:'<path d="M6 4h12v17l-6-4-6 4z"/>', search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>', check:'<path d="m5 12 4 4L19 6"/>', plus:'<path d="M12 5v14M5 12h14"/>', sliders:'<path d="M4 7h6m4 0h6M4 17h10m4 0h2"/><circle cx="12" cy="7" r="2"/><circle cx="16" cy="17" r="2"/>', sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>', basket:'<path d="M3 9h18l-2 11H5ZM7 9l3-6m7 6-3-6M9 12v5m6-5v5"/>', food:'<path d="M5 3v6a3 3 0 0 0 6 0V3M8 3v19m11 0V3c-4 2-4 9 0 10"/>', calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>'
};
function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.food}</svg>`; }
$$('[data-icon]').forEach(node => node.innerHTML = icon(node.dataset.icon));
let storedSaved = [];
try { const stored = JSON.parse(localStorage.getItem('dff:saved') || '[]'); if(Array.isArray(stored)) storedSaved = stored.filter(id => typeof id === 'string'); } catch {}
const state = { mode:new URLSearchParams(location.search).get('demo') === '1' ? 'demo' : 'live', view:'events', search:'', date:'all', freeEntry:false, signup:'all', savedOnly:false, savedIds:storedSaved, data:{events:[],sources:[]}, societies:[], societyLimit:30, loaded:false, activeEvent:null };
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 3500); }
function allEvents() { return state.mode === 'demo' ? demoEvents() : state.data.events; }
function chooseView(view, scroll = true) {
  state.view = view;
  $$('section.view').forEach(el => el.hidden = el.id !== `${view}-view`);
  $$('.nav-item').forEach(el => el.classList.toggle('active',el.dataset.view === view));
  if(scroll) $(`#${view}-view`).scrollIntoView({behavior:'smooth',block:'start'});
  if(view === 'societies') renderSocieties();
}
$$('[data-view]').forEach(el => el.addEventListener('click',() => chooseView(el.dataset.view)));
$('.brand').addEventListener('click',e => {e.preventDefault();chooseView('events',false);window.scrollTo({top:0,behavior:'smooth'});});
$('.hero-link').addEventListener('click',e => {e.preventDefault();chooseView('events');});
$('.skip-link').addEventListener('click',e => {e.preventDefault();chooseView('events');$('#event-search').focus();});
$('.footer-brand').addEventListener('click',e => {e.preventDefault();chooseView('events',false);window.scrollTo({top:0,behavior:'smooth'});});
function setMode(mode) {
  state.mode = mode; state.savedOnly = false;
  const url = new URL(location.href); mode === 'demo' ? url.searchParams.set('demo','1') : url.searchParams.delete('demo');
  history.replaceState({},'',url);
  render();
}
$('#live-mode').addEventListener('click',() => setMode('live'));
$('#demo-mode').addEventListener('click',() => setMode('demo'));
$('#return-live').addEventListener('click',() => setMode('live'));
$('#event-search').addEventListener('input',e => {state.search=e.target.value;render();});
$$('[data-date]').forEach(button => button.addEventListener('click',() => {state.date=button.dataset.date;render();}));
$('#filters-toggle').addEventListener('click',() => {const expanded=$('#more-filters').hidden;$('#more-filters').hidden=!expanded;$('#filters-toggle').setAttribute('aria-expanded',String(expanded));});
$('#free-entry').addEventListener('change',e => {state.freeEntry=e.target.checked;render();});
$('#signup-filter').addEventListener('change',e => {state.signup=e.target.value;render();});
function resetFilters(){state.search='';state.date='all';state.freeEntry=false;state.signup='all';state.savedOnly=false;$('#event-search').value='';$('#free-entry').checked=false;$('#signup-filter').value='all';render();}
$('#reset-filters').addEventListener('click',resetFilters);
$('#saved-toggle').addEventListener('click',() => {state.savedOnly=!state.savedOnly;chooseView('events');render();});
document.addEventListener('keydown',e => { if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!document.querySelector('dialog[open]')) {e.preventDefault();chooseView('events');$('#event-search').focus();} });
function saveEvent(id) {
  const exists=state.savedIds.includes(id);
  state.savedIds=exists?state.savedIds.filter(saved=>saved!==id):[...state.savedIds,id];
  try {localStorage.setItem('dff:saved',JSON.stringify(state.savedIds));} catch {toast('Saved for this visit. Browser storage is unavailable.');render();return;}
  render(); toast(exists?'Removed from your saved events.':'Saved to your food plan.');
  if(state.activeEvent) updateDialogSave();
}
function card(event) {
  const status=eventStatus(event),started=status!=='upcoming';
  const foodIcons=[['pizza','🍕','#F7DDAA'],['coffee','🥐','#E6DDF0'],['curry','🍛','#DDE9D6'],['snack','🍿','#F7DAD6'],['cake','🧁','#F0DDEC']];
  const visual=foodIcons.find(([keyword])=>`${event.title} ${event.food}`.toLowerCase().includes(keyword))||['','🥪','#E6DDF0'];
  const signup=signupLabel(event);
  return `<article class="event-card ${started?'started':''}"><div class="card-art" style="--art-bg:${event.demo?event.color:visual[2]}"><div class="date-stamp"><strong>${formatDate(event.starts_at,{day:'numeric'})}</strong><span>${formatDate(event.starts_at,{month:'short'})}</span></div><span class="food-drawing" aria-hidden="true">${escape(event.demo?event.emoji:visual[1])}</span><button class="bookmark-button" data-save="${escape(event.id)}" aria-label="${state.savedIds.includes(event.id)?'Unsave':'Save'} ${escape(event.title)}" aria-pressed="${state.savedIds.includes(event.id)}">${icon('bookmark')}</button>${started?`<span class="started-tag">${status==='ended'?'Ended today':'Already started'}</span>`:''}</div><div class="card-body"><div class="society-label"><span class="society-dot" aria-hidden="true">${escape(event.society.slice(0,1))}</span>${escape(event.society)}</div><h3 class="event-title"><button data-details="${escape(event.id)}">${escape(event.title)}</button></h3><div class="event-meta"><div>${icon('clock')}<span>${friendlyDay(event.starts_at)} · ${formatDate(event.starts_at,{hour:'2-digit',minute:'2-digit'})}${event.ends_at?'–'+formatDate(event.ends_at,{hour:'2-digit',minute:'2-digit'}):''}</span></div><div>${icon('pin')}<span>${escape(event.location||'Location to be confirmed')}</span></div><div class="food-meta">${icon('food')}<span>${escape(event.food||'Free food')}</span></div></div><div class="card-tags"><span class="tag ${event.signup_required===false?'free':event.signup_required===true?'warning':''}">${signup}</span>${event.demo?'<span class="tag example">Example event</span>':'<span class="tag free">Free food confirmed</span>'}</div><div class="card-bottom"><span class="card-price">${entryLabel(event.price)}<small>${event.price>0?'Food included with entry':'Food on the house'}</small></span><button class="details-button" data-details="${escape(event.id)}">View details <span aria-hidden="true">↗</span></button></div></div></article>`;
}
function render() {
  const focused=document.activeElement;
  const focusKey=focused?.closest('#event-grid') ? {save:focused.dataset.save,details:focused.dataset.details,isTitle:!!focused.closest('.event-title')} : null;
  $('#demo-banner').hidden=state.mode!=='demo';
  ['live','demo'].forEach(mode => {const el=$(`#${mode}-mode`);el.classList.toggle('selected',state.mode===mode);el.setAttribute('aria-pressed',String(state.mode===mode));});
  $$('[data-date]').forEach(el => {el.classList.toggle('active',el.dataset.date===state.date);el.setAttribute('aria-pressed',String(el.dataset.date===state.date));});
  const all=allEvents(), shown=filterEvents(all,state), count=shown.length;
  const activeSaved=all.filter(e=>eventStatus(e)!=='past'&&state.savedIds.includes(e.id)).length;
  $('#saved-count').textContent=activeSaved;$('#saved-toggle').setAttribute('aria-pressed',String(state.savedOnly));
  const filterCount=Number(state.freeEntry)+Number(state.signup!=='all');$('#filter-count').hidden=!filterCount;$('#filter-count').textContent=filterCount;
  $('#results-count').textContent=!state.loaded&&state.mode==='live'?'Checking the menu…':`${count} ${state.mode==='demo'?'example ':''}${state.savedOnly?'saved ':''}event${count===1?'':'s'}${state.date==='today'?' today':state.date==='tomorrow'?' tomorrow':''}`;
  $('#event-grid').innerHTML=shown.map(card).join('');
  $('#event-grid').hidden=!count;
  const empty=$('#empty-state');empty.hidden=count>0||(!state.loaded&&state.mode==='live');
  const noLive=state.mode==='live'&&filterEvents(all).length===0;
  $('#empty-title').textContent=state.savedOnly?'Your food plan starts here.':noLive?'Nothing confirmed on the menu yet.':'No bites match those filters.';
  $('#empty-copy').textContent=state.savedOnly?'Tap the bookmark on an event to save it on this device.':noLive?'We haven’t verified an upcoming free-food event from the sources checked. Explore the demo, browse the societies, or suggest a post.':'Try another date, a different search, or fewer filters.';
  $('#empty-action').textContent=state.savedOnly?'Browse events':noLive?'Explore example events':'Clear filters';
  $('#empty-action').onclick=state.savedOnly?resetFilters:noLive?()=>setMode('demo'):resetFilters;
  $$('[data-save]').forEach(el=>el.addEventListener('click',()=>saveEvent(el.dataset.save)));
  $$('[data-details]').forEach(el=>el.addEventListener('click',()=>showDetails(el.dataset.details)));
  if(focusKey){
    const replacement=$$(focusKey.save?'[data-save]':'[data-details]').find(el=>focusKey.save?el.dataset.save===focusKey.save:el.dataset.details===focusKey.details&&!!el.closest('.event-title')===focusKey.isTitle);
    if(replacement)replacement.focus({preventScroll:true});
    else $('#saved-toggle').focus({preventScroll:true});
  }
}
function showDetails(id) {
  const event=allEvents().find(e=>e.id===id);if(!event)return;state.activeEvent=event;
  const date=formatDate(event.starts_at,{weekday:'long',day:'numeric',month:'long',year:'numeric'});
  const deadline=event.signup_required===false?'Not needed':event.signup_deadline?formatDate(event.signup_deadline,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'}):'Not stated — check the organiser';
  const mapUrl=`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent((event.location||'')+' Durham UK')}`;
  $('#event-dialog-content').innerHTML=`<span class="dialog-eyebrow">${escape(event.society)}</span><h2 id="dialog-title">${escape(event.title)}</h2>${event.demo?'<p class="demo-detail-note">Example event. This listing is only here to demonstrate the prototype. Please don’t travel to it.</p>':''}<p>${escape(event.description||'Check the original event for the latest details.')}</p><dl class="detail-list"><dt>When</dt><dd>${date}<br>${formatDate(event.starts_at,{hour:'2-digit',minute:'2-digit'})}${event.ends_at?'–'+formatDate(event.ends_at,{hour:'2-digit',minute:'2-digit'}):''} (UK time)</dd><dt>Where</dt><dd>${escape(event.location||'Not confirmed')}${!event.demo&&event.location?`<br><a href="${escape(mapUrl)}" target="_blank" rel="noopener noreferrer">Open directions ↗</a>`:''}</dd><dt>On the menu</dt><dd>${escape(event.food||'Free food')}</dd><dt>Entry</dt><dd>${entryLabel(event.price)}</dd><dt>Sign-up</dt><dd>${signupLabel(event)}</dd><dt>Deadline</dt><dd>${deadline}</dd><dt>Who can go</dt><dd>${escape(event.eligibility||'Not stated — check the organiser')}</dd><dt>Dietary options</dt><dd>${escape(event.dietary?.length?event.dietary.join(', '):'Not stated — ask the organiser')}</dd></dl><div class="dialog-actions">${!event.demo&&safeUrl(event.source_url)?`<a class="primary-button" href="${escape(safeUrl(event.source_url))}" target="_blank" rel="noopener noreferrer">Original event ↗</a>`:''}<button id="dialog-save" class="secondary-button"></button><button id="add-calendar" class="secondary-button">${icon('calendar')}Add to calendar</button></div><p class="detail-note">${event.demo?'Calendar downloads are clearly marked as demo events.':`Source: ${escape(event.source_label||'Original organiser listing')}. ${event.verified_at?'Checked '+formatDate(event.verified_at,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})+'.':''} Details can change; check the original post before setting off.`}</p>`;
  updateDialogSave();$('#dialog-save').addEventListener('click',()=>saveEvent(event.id));
  $('#add-calendar').addEventListener('click',()=> { const blob=new Blob([calendarFile(event)],{type:'text/calendar;charset=utf-8'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download=`${event.demo?'DEMO-':''}durham-food-${String(event.id).replace(/[^a-zA-Z0-9-]/g,'')}.ics`;link.click();setTimeout(()=>URL.revokeObjectURL(url),2000);toast('Calendar file downloaded.'); });
  $('#event-dialog').showModal();
}
function updateDialogSave(){if(!$('#dialog-save'))return;const saved=state.savedIds.includes(state.activeEvent.id);$('#dialog-save').innerHTML=icon('bookmark')+(saved?' Saved':' Save event');$('#dialog-save').setAttribute('aria-pressed',String(saved));}
$$('[data-close]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.close).close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}}));
$('#event-dialog').addEventListener('close',()=>state.activeEvent=null);
$('#submit-event').addEventListener('click',()=>$('#submit-dialog').showModal());
$('#suggest-form').addEventListener('submit',event=>{
  event.preventDefault();const data=new FormData(event.target);const url=safeUrl(data.get('source'));if(!url){toast('Use a full http or https event link.');return;}
  const issue=new URL('https://github.com/chrislovescoding/Durham-free-food/issues/new');issue.searchParams.set('title',`Event suggestion: ${data.get('title')}`);issue.searchParams.set('body',`Original source: ${url}\n\nDetails:\n${data.get('details')||'No further details provided.'}\n\nPlease review the source, free-food offer, event date, entry price, eligibility and sign-up requirements before publishing.`);
  window.open(issue.href,'_blank','noopener,noreferrer');
});
$('#society-search').addEventListener('input',()=>{state.societyLimit=30;renderSocieties();});
$('#load-societies').addEventListener('click',()=>{state.societyLimit+=30;renderSocieties();});
function renderSocieties(){
  const search=$('#society-search').value.trim().toLowerCase();const matches=state.societies.filter(s=>s.name.toLowerCase().includes(search));
  $('#society-count').textContent=state.loaded?`${matches.length} ${matches.length===1?'society':'societies'}${search?' matching your search':''} · Links go to their original pages`:'Loading societies…';
  $('#society-grid').innerHTML=matches.slice(0,state.societyLimit).map(s=>`<article class="society-card"><span class="society-avatar" aria-hidden="true">${escape(s.name.slice(0,1))}</span><div><h3><a href="${escape(safeUrl(s.url)||'https://www.durhamsu.com/groups/studentgroups/')}" target="_blank" rel="noopener noreferrer">${escape(s.name)}</a></h3><div class="society-links"><a href="${escape(safeUrl(s.url)||'https://www.durhamsu.com/groups/studentgroups/')}" target="_blank" rel="noopener noreferrer">SU profile ↗</a>${safeUrl(s.instagram_url)?`<a href="${escape(safeUrl(s.instagram_url))}" target="_blank" rel="noopener noreferrer">Instagram ↗</a>`:''}${safeUrl(s.facebook_url)?`<a href="${escape(safeUrl(s.facebook_url))}" target="_blank" rel="noopener noreferrer">Facebook ↗</a>`:''}</div></div></article>`).join('');
  if(!matches.length&&state.loaded)$('#society-grid').innerHTML='<p class="muted">No societies found. Try a shorter search, or open the SU directory above.</p>';
  $('#load-societies').hidden=matches.length<=state.societyLimit;
}
async function fetchJson(url){const response=await fetch(url,{signal:AbortSignal.timeout(7000),cache:'no-cache'});if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();}
async function loadFeed(name) {
  const local=fetchJson(`./data/${name}.json`).then(value=>({value,origin:'bundled'}));
  const remote=fetchJson(`https://raw.githubusercontent.com/chrislovescoding/Durham-free-food/main/dist/data/${name}.json`).then(value=>({value,origin:'repository'}));
  const results=await Promise.allSettled([local,remote]);
  const valid=results.filter(r=>r.status==='fulfilled'&&Array.isArray(r.value.value[name])).map(r=>r.value);
  if(!valid.length)throw new Error('No feed available');
  valid.sort((a,b)=>(Date.parse(b.value.updated_at)||0)-(Date.parse(a.value.updated_at)||0));return valid[0];
}
function renderCoverage() {
  const updated=state.data.updated_at;
  const age=updated?Date.now()-new Date(updated).getTime():Infinity;
  const healthy=state.data.status?.state==='ok'||state.data.status?.state==='success';
  $('#freshness-text').textContent=updated?`Last check ${formatDate(updated,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})} UK time. Evening checks around 8pm.`:'No source check is available yet.';
  if(age>36*3600000){$('#feed-warning').hidden=false;$('#feed-warning').textContent='The latest feed is more than a day old. Check the original event links for updates.';}
  else if(state.data.status?.state&&!healthy){$('#feed-warning').hidden=false;$('#feed-warning').textContent='Some sources could not be checked. Existing listings retain their last checked dates.';}
  const sources=Array.isArray(state.data.sources)?state.data.sources:[];
  const eventSourceCount=sources.filter(source=>source.type!=='directory').length;
  $('#coverage-summary').textContent=`${state.societies.length} societies in the directory. ${eventSourceCount} configured public event source${eventSourceCount===1?'':'s'}. Instagram and Facebook posts are not automatically collected by this prototype.`;
  $('#source-status').innerHTML=sources.map(source=>{const url=safeUrl(source.url||source.source_url);const title=source.name||source.label||source.id||'Event source';const status=source.status||'Not checked';return `<div class="source-row">${url?`<a href="${escape(url)}" target="_blank" rel="noopener noreferrer">${escape(title)}</a>`:`<strong>${escape(title)}</strong>`}<span>${escape(status)}${typeof source.parsed_count==='number'?` · ${source.parsed_count} entries checked`:''}</span></div>`;}).join('')||'<p class="muted">No source report is available yet.</p>';
}
async function init(){
  render();
  const [events,societies]=await Promise.allSettled([loadFeed('events'),loadFeed('societies')]);
  if(events.status==='fulfilled'){state.data=events.value.value;state.data.events=state.data.events.filter(isValidEvent);}else{$('#feed-warning').hidden=false;$('#feed-warning').textContent='The live feed could not be loaded. Refresh to try again, or explore the clearly labelled demo.';}
  if(societies.status==='fulfilled')state.societies=societies.value.value.societies.filter(s=>typeof s.name==='string').sort((a,b)=>a.name.localeCompare(b.name));
  state.loaded=true;render();renderSocieties();renderCoverage();
}
init();
// Re-evaluate event status and London midnight expiry while the page stays open.
setInterval(()=>{if(state.loaded)render();},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.loaded)render();});
