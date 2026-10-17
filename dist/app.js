import { eventStatus, filterEvents, formatDate, friendlyDay, entryLabel, signupLabel, calendarFile, safeUrl, isValidEvent } from './events.js';
import { renderEventDetails } from './event-details.js';
import { buildGridItems } from './promotions.js';

const $ = selector => document.querySelector(selector);
const $$ = selector => [...document.querySelectorAll(selector)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const paths = {
  'arrow-down':'<path d="M12 4v16m-6-6 6 6 6-6"/>',
  bookmark:'<path d="M6 4h12v17l-6-4-6 4z"/>', search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 4 4"/>', clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', pin:'<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>', check:'<path d="m5 12 4 4L19 6"/>', plus:'<path d="M12 5v14M5 12h14"/>', sliders:'<path d="M4 7h6m4 0h6M4 17h10m4 0h2"/><circle cx="12" cy="7" r="2"/><circle cx="16" cy="17" r="2"/>', sparkles:'<path d="m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5ZM20 2v4m-2-2h4"/>', basket:'<path d="M3 9h18l-2 11H5ZM7 9l3-6m7 6-3-6M9 12v5m6-5v5"/>', food:'<path d="M5 3v6a3 3 0 0 0 6 0V3M8 3v19m11 0V3c-4 2-4 9 0 10"/>', calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>'
};
function icon(name) { return `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.food}</svg>`; }
$$('[data-icon]').forEach(node => node.innerHTML = icon(node.dataset.icon));
let storedSaved = [];
try { const stored = JSON.parse(localStorage.getItem('dff:saved') || '[]'); if(Array.isArray(stored)) storedSaved = stored.filter(id => typeof id === 'string'); } catch {}
const state = { search:'', date:'recent', freeEntry:false, signup:'all', savedOnly:false, savedIds:storedSaved, data:{events:[]}, loaded:false, loadError:false, activeEvent:null };
let toastTimer;
function toast(message) { $('#toast').textContent = message; $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 3500); }
function allEvents() { return state.data.events; }
function focusFinder(){ $('#finder').scrollIntoView({behavior:'smooth',block:'start'}); }
$('.brand').addEventListener('click',e=>{e.preventDefault();window.scrollTo({top:0,behavior:'smooth'});});
$('.footer-brand').addEventListener('click',e=>{e.preventDefault();window.scrollTo({top:0,behavior:'smooth'});});
$('.skip-link').addEventListener('click',e=>{e.preventDefault();focusFinder();$('#event-search').focus();});
$('#event-search').addEventListener('input',e => {state.search=e.target.value;render();});
$$('[data-date]').forEach(button => button.addEventListener('click',() => {state.date=button.dataset.date;render();}));
$('#filters-toggle').addEventListener('click',() => {const expanded=$('#more-filters').hidden;$('#more-filters').hidden=!expanded;$('#filters-toggle').setAttribute('aria-expanded',String(expanded));});
$('#free-entry').addEventListener('change',e => {state.freeEntry=e.target.checked;render();});
$('#signup-filter').addEventListener('change',e => {state.signup=e.target.value;render();});
function resetFilters(){state.search='';state.date='recent';state.freeEntry=false;state.signup='all';state.savedOnly=false;$('#event-search').value='';$('#free-entry').checked=false;$('#signup-filter').value='all';render();}
$('#reset-filters').addEventListener('click',resetFilters);
$('#saved-toggle').addEventListener('click',() => {state.savedOnly=!state.savedOnly;if(state.savedOnly)state.date='all';focusFinder();render();});
document.addEventListener('keydown',e => { if(e.key==='/'&&!['INPUT','TEXTAREA','SELECT'].includes(document.activeElement.tagName)&&!document.querySelector('dialog[open]')) {e.preventDefault();focusFinder();$('#event-search').focus();} });
function saveEvent(id) {
  const exists=state.savedIds.includes(id);
  state.savedIds=exists?state.savedIds.filter(saved=>saved!==id):[...state.savedIds,id];
  try {localStorage.setItem('dff:saved',JSON.stringify(state.savedIds));} catch {toast('Saved for this visit. Browser storage is unavailable.');render();return;}
  render(); toast(exists?'Removed from your saved events.':'Saved to your food plan.');
  if(state.activeEvent) updateDialogSave();
}
function advertCard(advert) {
  return `<aside class="advert-card advert-${escape(advert.theme)}" aria-label="Advertisement — demo placement"><div class="advert-label"><span>Advertisement</span><span>Demo placement</span></div><div class="advert-copy"><img class="advert-symbol" src="./assets/grubr-symbol.svg" width="621" height="794" alt=""><h3>${escape(advert.title)}</h3><p>${escape(advert.description)}</p><button class="advert-cta" data-advertise>${escape(advert.action)} <span aria-hidden="true">↗</span></button></div></aside>`;
}
function card(event, promotion = null) {
  const status=eventStatus(event),started=status!=='upcoming';
  const saved=state.savedIds.includes(event.id);
  const conditions=[...(event.price>0?[entryLabel(event.price)]:[]),...(!started&&event.signup_required===true?[signupLabel(event)]:[])];
  return `<article class="event-card ${promotion?'is-sponsored ':''}${started?'started':''}">
    ${promotion?`<button class="sponsor-ribbon" data-advertise aria-label="About this sponsored ${promotion.demo?'demo ':''}placement"><span>${icon('sparkles')} Sponsored</span><span>${promotion.demo?'Demo placement':'Promoted event'} ↗</span></button>`:''}
    <div class="card-body">
      <div class="card-topline"><time class="card-date" datetime="${escape(event.starts_at)}">${friendlyDay(event.starts_at)}</time><button class="bookmark-button" data-save="${escape(event.id)}" aria-label="${saved?'Unsave':'Save'} ${escape(event.title)}" aria-pressed="${saved}">${icon('bookmark')}</button></div>
      <p class="society-label">${escape(event.society)}</p>
      <h3 class="event-title"><button data-details="${escape(event.id)}">${escape(event.title)}</button></h3>
      <div class="event-meta"><div>${icon('clock')}<span>${formatDate(event.starts_at,{hour:'2-digit',minute:'2-digit'})}${event.ends_at?'–'+formatDate(event.ends_at,{hour:'2-digit',minute:'2-digit'}):''}</span></div><div>${icon('pin')}<span>${escape(event.location||'Location to be confirmed')}</span></div></div>
      <div class="card-offer">${icon('food')}<p>${escape(event.food||'Free food')}</p></div>
      <div class="card-bottom"><div class="card-conditions">${started?`<span class="past-label">${status==='past'?'Past event':status==='ended'?'Ended today':'Already started'}</span>`:''}${conditions.map(label=>`<span>${escape(label)}</span>`).join('')}</div><button class="details-button" data-details="${escape(event.id)}">Details <span aria-hidden="true">↗</span></button></div>
    </div></article>`;
}
function render() {
  const focused=document.activeElement;
  const focusKey=focused?.closest('#event-grid') ? {save:focused.dataset.save,details:focused.dataset.details,isTitle:!!focused.closest('.event-title')} : null;
  $$('[data-date]').forEach(el => {el.classList.toggle('active',el.dataset.date===state.date);el.setAttribute('aria-pressed',String(el.dataset.date===state.date));});
  const all=allEvents(), shown=filterEvents(all,state), count=shown.length;
  const activeSaved=all.filter(e=>state.savedIds.includes(e.id)).length;
  $('#saved-count').textContent=activeSaved;$('#saved-toggle').setAttribute('aria-pressed',String(state.savedOnly));
  const filterCount=Number(state.freeEntry)+Number(state.signup!=='all');$('#filter-count').hidden=!filterCount;$('#filter-count').textContent=filterCount;
  $('#results-count').textContent=!state.loaded?'Checking the menu…':`${count} ${state.savedOnly?'saved ':''}event${count===1?'':'s'}${state.date==='today'?' today':state.date==='tomorrow'?' tomorrow':''}`;
  const statuses=shown.map(e=>eventStatus(e));
  const upcoming=statuses.filter(s=>s==='upcoming').length, ongoing=statuses.filter(s=>s==='started').length;
  const past=statuses.filter(s=>s==='past'||s==='ended').length;
  $('#history-summary').textContent=state.loaded?[`${upcoming} upcoming`,...(ongoing?[`${ongoing} already started`]:[]),`${past} past`].join(' · '):'';
  $('#range-note').textContent=({recent:'All upcoming events plus the last 14 days. Past events are grey and have already happened.',upcoming:'Upcoming events and any still in progress. Check the original post before going.',past:'All past events we have confirmed, newest first. These events have already happened.',all:'Every event we have confirmed. Upcoming first, then past events from newest to oldest.',today:'Events dated today in Durham. Finished events are marked clearly.',tomorrow:'Events dated tomorrow in Durham.',week:'Events over the next seven calendar days in Durham.'})[state.date];
  const placements=buildGridItems(shown,{adsEnabled:!state.savedOnly&&!state.search.trim()&&state.date!=='past'});
  $('#event-grid').innerHTML=placements.map(item=>item.kind==='advert'?advertCard(item.advert):card(item.event,item.promotion)).join('');
  $('#event-grid').hidden=!count;
  const empty=$('#empty-state');empty.hidden=count>0||(!state.loaded);
  const noLive=all.length===0;
  $('#empty-title').textContent=state.loadError?'The menu couldn’t load.':state.savedOnly?'Your food plan starts here.':noLive?'Nothing on the menu yet.':'No bites match those filters.';
  $('#empty-copy').textContent=state.loadError?'Please try loading the events again.':state.savedOnly?'Tap the bookmark on an event to save it on this device.':noLive?'Know of a free-food event? Share the original post and help us add it.':'Try another date, a different search, or fewer filters.';
  $('#empty-action').textContent=state.loadError?'Try again':state.savedOnly?'Browse events':noLive?'Suggest an event':'Clear filters';
  $('#empty-action').onclick=state.loadError?init:state.savedOnly?resetFilters:noLive?openSuggestions:resetFilters;
  $$('[data-save]').forEach(el=>el.addEventListener('click',()=>saveEvent(el.dataset.save)));
  $$('[data-details]').forEach(el=>el.addEventListener('click',()=>showDetails(el.dataset.details)));
  $$('[data-advertise]').forEach(el=>el.addEventListener('click',()=>$('#sponsor-dialog').showModal()));
  if(focusKey){
    const replacement=$$(focusKey.save?'[data-save]':'[data-details]').find(el=>focusKey.save?el.dataset.save===focusKey.save:el.dataset.details===focusKey.details&&!!el.closest('.event-title')===focusKey.isTitle);
    if(replacement)replacement.focus({preventScroll:true});
    else $('#saved-toggle').focus({preventScroll:true});
  }
}
function showDetails(id) {
  const event=allEvents().find(e=>e.id===id);if(!event)return;state.activeEvent=event;
  $('#event-dialog-content').innerHTML=renderEventDetails(event);
  updateDialogSave();$('#dialog-save').addEventListener('click',()=>saveEvent(event.id));
  $('#add-calendar').addEventListener('click',()=> {
    if(['past','ended'].includes(eventStatus(event)))return;
    const blob=new Blob([calendarFile(event)],{type:'text/calendar;charset=utf-8'}),url=URL.createObjectURL(blob),link=document.createElement('a');
    link.href=url;link.download=`grubr-${String(event.id).replace(/[^a-zA-Z0-9-]/g,'')}.ics`;link.click();setTimeout(()=>URL.revokeObjectURL(url),2000);toast('Calendar file downloaded.');
  });
  $('#event-dialog').showModal();
}
function updateDialogSave(){if(!$('#dialog-save'))return;const saved=state.savedIds.includes(state.activeEvent.id);$('#dialog-save').innerHTML=icon('bookmark')+(saved?' Saved':' Save event');$('#dialog-save').setAttribute('aria-pressed',String(saved));}
$$('[data-close]').forEach(button=>button.addEventListener('click',()=>document.getElementById(button.dataset.close).close()));
$$('dialog').forEach(dialog=>dialog.addEventListener('click',event=>{if(event.target===dialog){const rect=dialog.getBoundingClientRect();if(event.clientX<rect.left||event.clientX>rect.right||event.clientY<rect.top||event.clientY>rect.bottom)dialog.close();}}));
$('#event-dialog').addEventListener('close',()=>{state.activeEvent=null;$('#event-dialog-content').replaceChildren();});
let suggestionAttempt=null;
function openSuggestions(){
  if(!$('#suggest-success').hidden){$('#suggest-form').reset();suggestionAttempt=null;}
  $('#suggest-form').hidden=false;$('#suggest-success').hidden=true;$('.suggest-intro').hidden=false;$('#suggest-error').hidden=true;
  $('#submit-dialog').showModal();
}
$('#submit-event').addEventListener('click',openSuggestions);
$('#suggest-form').addEventListener('submit',async event=>{
  event.preventDefault();
  const form=event.target,fields=new FormData(form),source=safeUrl(fields.get('source'));
  const error=$('#suggest-error'),button=$('#send-suggestion');
  error.hidden=true;
  if(!source){error.textContent='Please add a full event or post link, beginning with https://.';error.hidden=false;return;}
  const payload={title:String(fields.get('title')||'').trim(),source_url:source,details:String(fields.get('details')||'').trim(),website:String(fields.get('website')||'')};
  const signature=JSON.stringify(payload);
  if(!suggestionAttempt||suggestionAttempt.signature!==signature)suggestionAttempt={signature,id:crypto.randomUUID()};
  button.disabled=true;button.textContent='Sending…';
  try{
    const response=await fetch('/api/suggestions',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,idempotency_key:suggestionAttempt.id}),signal:AbortSignal.timeout(15000)});
    const result=await response.json().catch(()=>({}));
    if(!response.ok)throw new Error(response.status===429?'A few too many suggestions at once. Please try again in a little while.':response.status===400?'Please check the event name and link, then try again.':'We couldn’t save your suggestion. Your details are still here—please try again.');
    if(result.status!=='pending'||typeof result.id!=='string')throw new Error('We couldn’t confirm your suggestion was saved. Please try again.');
    form.hidden=true;$('.suggest-intro').hidden=true;$('#suggest-success').hidden=false;suggestionAttempt=null;
    $('#suggest-success button').focus();
  }catch(problem){error.textContent=problem.name==='TimeoutError'||problem.name==='TypeError'?'The connection was interrupted. Your details are still here—please try again.':problem.message;error.hidden=false;}
  finally{button.disabled=false;button.textContent='Send suggestion';}
});
async function fetchJson(url){const response=await fetch(url,{signal:AbortSignal.timeout(7000),cache:'no-cache'});if(!response.ok)throw new Error(`HTTP ${response.status}`);return response.json();}
async function loadFeed(name, arrayKey=name) {
  const local=fetchJson(`./data/${name}.json`).then(value=>({value,origin:'bundled'}));
  const remote=fetchJson(`https://raw.githubusercontent.com/chrislovescoding/Durham-free-food/main/dist/data/${name}.json`).then(value=>({value,origin:'repository'}));
  const results=await Promise.allSettled([local,remote]);
  const valid=results.filter(r=>r.status==='fulfilled'&&Array.isArray(r.value.value[arrayKey])).map(r=>r.value);
  if(!valid.length)throw new Error('No feed available');
  valid.sort((a,b)=>(Date.parse(b.value.updated_at)||0)-(Date.parse(a.value.updated_at)||0));return valid[0];
}
function renderFreshness(){
  const updated=state.data.updated_at;
  $('#freshness-text').textContent=updated?`Listings updated ${formatDate(updated,{day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'})}. Times shown are local to Durham.`:'';
}
async function init(){
  state.loaded=false;state.loadError=false;render();
  try {const feed=await loadFeed('events');state.data=feed.value;state.data.events=state.data.events.filter(isValidEvent);}
  catch {state.loadError=true;}
  state.loaded=true;render();renderFreshness();
}
init();
setInterval(()=>{if(state.loaded)render();},60000);
document.addEventListener('visibilitychange',()=>{if(!document.hidden&&state.loaded)render();});
