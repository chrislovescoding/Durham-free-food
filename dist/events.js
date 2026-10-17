export const TIME_ZONE = 'Europe/London';
export function dayKey(date = new Date()) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(date));
}
export function addDays(key, count) {
  const d = new Date(`${key}T12:00:00Z`); d.setUTCDate(d.getUTCDate() + count); return d.toISOString().slice(0, 10);
}
export function safeUrl(value) {
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : ''; } catch { return ''; }
}
export function eventStatus(event, now = new Date()) {
  if (dayKey(event.starts_at) < dayKey(now)) return 'past';
  if (new Date(event.starts_at) <= now) return event.ends_at && new Date(event.ends_at) <= now ? 'ended' : 'started';
  return 'upcoming';
}
export function isValidEvent(event) {
  return Boolean(event && event.id && event.title && event.society && Number.isFinite(Date.parse(event.starts_at)) && /(?:Z|[+-]\d{2}:\d{2})$/.test(event.starts_at) && safeUrl(event.source_url) && event.food_is_free === true && (event.price === null || (typeof event.price === 'number' && event.price >= 0)));
}
export function filterEvents(events, filters = {}, now = new Date()) {
  const today = dayKey(now); const search = (filters.search || '').trim().toLocaleLowerCase('en-GB');
  return events.filter(event => {
    const key = dayKey(event.starts_at);
    if (key < today) return false;
    if (filters.date === 'today' && key !== today) return false;
    if (filters.date === 'tomorrow' && key !== addDays(today, 1)) return false;
    if (filters.date === 'week' && key >= addDays(today, 7)) return false;
    if (filters.freeEntry && event.price !== 0) return false;
    if (filters.signup === 'none' && event.signup_required !== false) return false;
    if (filters.signup === 'required' && event.signup_required !== true) return false;
    if (filters.savedOnly && !filters.savedIds?.includes(event.id)) return false;
    if (search && ![event.title, event.society, event.location, event.food, event.description, ...(event.dietary || [])].filter(Boolean).join(' ').toLocaleLowerCase('en-GB').includes(search)) return false;
    return true;
  }).sort((a,b) => new Date(a.starts_at) - new Date(b.starts_at));
}
export function formatDate(date, options = {}) { return new Intl.DateTimeFormat('en-GB', { timeZone: TIME_ZONE, ...options }).format(new Date(date)); }
export function friendlyDay(date, now = new Date()) { const key = dayKey(date), today = dayKey(now); return key === today ? 'Today' : key === addDays(today,1) ? 'Tomorrow' : formatDate(date,{weekday:'short',day:'numeric',month:'short'}); }
export function entryLabel(price) { return price === null || price === undefined ? 'Entry price unconfirmed' : price === 0 ? 'Free entry' : `£${Number(price).toFixed(price % 1 ? 2 : 0)} entry`; }
export function signupLabel(event, now = new Date()) {
  if (event.signup_required === false) return 'Just turn up';
  if (event.signup_required === null || event.signup_required === undefined) return 'Sign-up unconfirmed';
  if (event.signup_deadline && new Date(event.signup_deadline) <= now) return 'Sign-up closed';
  return 'Sign-up required';
}
function icsEscape(value) { return String(value || '').replaceAll('\\','\\\\').replaceAll('\n','\\n').replaceAll(',','\\,').replaceAll(';','\\;').replaceAll('\r',''); }
function icsDate(date) { return new Date(date).toISOString().replace(/[-:]/g,'').replace(/\.\d{3}Z$/,'Z'); }
function foldIcsLine(line) { let result = '', chunk = ''; for (const char of line) { if (new TextEncoder().encode(chunk + char).length > 74) { result += chunk + '\r\n'; chunk = ' '; } chunk += char; } return result + chunk; }
export function calendarFile(event, now = new Date()) {
  const lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//Grubr//Events//EN','CALSCALE:GREGORIAN','BEGIN:VEVENT',`UID:${icsEscape(event.id)}@durhamfreefood`,`DTSTAMP:${icsDate(now)}`,`DTSTART:${icsDate(event.starts_at)}`];
  if(event.ends_at) lines.push(`DTEND:${icsDate(event.ends_at)}`);
  lines.push(`SUMMARY:${icsEscape((event.demo ? '[DEMO — not a real event] ' : '') + event.title)}`,`LOCATION:${icsEscape(event.location)}`,`DESCRIPTION:${icsEscape((event.demo ? 'Example only. Do not attend.\n' : '') + event.description + '\n' + entryLabel(event.price))}`);
  if(safeUrl(event.source_url)) lines.push(`URL:${safeUrl(event.source_url)}`);
  lines.push('END:VEVENT','END:VCALENDAR'); return lines.map(foldIcsLine).join('\r\n') + '\r\n';
}
function londonTime(key, hour, minute = 0) {
  const guess = new Date(`${key}T${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')}:00Z`);
  const offset = formatDate(guess,{timeZoneName:'shortOffset'}).match(/GMT([+-]\d+)/)?.[1] || '0';
  return new Date(guess.getTime() - Number(offset) * 3600000).toISOString();
}
export function demoEvents(now = new Date()) {
  const today = dayKey(now);
  const entries = [
    {id:'demo-pizza',title:'Pizza & a little friendly debate',society:'Example Debating Society',food:'Pizza, including veggie options',location:'Dunelm House · an example room',offset:0,hour:18,minute:30,price:0,signup_required:false,emoji:'🍕',color:'#F7DDAA',description:'A relaxed welcome evening with a few big questions and plenty of pizza. This is an illustrative listing, not an actual society event.',dietary:['Vegetarian options']},
    {id:'demo-coffee',title:'A coffee break worth taking',society:'Example International Society',food:'Coffee, tea & pastries',location:'Student café · example location',offset:1,hour:11,price:0,signup_required:false,emoji:'🥐',color:'#E6DDF0',description:'Meet someone new over a hot drink and a pastry. This example shows how an informal drop-in would appear.',dietary:[]},
    {id:'demo-curry',title:'Curry, conversation & community',society:'Example Cultural Society',food:'A free vegetarian curry',location:'Students’ Union · example venue',offset:2,hour:19,price:0,signup_required:true,emoji:'🍛',color:'#DDE9D6',description:'An example community dinner with advance registration. The prototype displays sign-up deadlines separately from the event time.',dietary:['Vegetarian']},
    {id:'demo-games',title:'Board games. Big snacks.',society:'Example Games Society',food:'Popcorn, crisps & snacks',location:'City centre · example venue',offset:3,hour:17,price:2,signup_required:false,emoji:'🍿',color:'#F7DAD6',description:'An example of free food at an event with an entry fee. Use “Free entry only” to hide events like this.',dietary:[]},
    {id:'demo-doughnuts',title:'Something sweet, someone new',society:'Example Volunteering Society',food:'Doughnuts & hot chocolate',location:'Palatine Centre · example venue',offset:5,hour:14,price:0,signup_required:true,emoji:'🍩',color:'#E2DFF4',description:'An illustrative volunteering meet-up with a sweet treat. Save it to try planning your week.',dietary:['Vegan options']},
    {id:'demo-breakfast',title:'Breakfast before the books',society:'Example Study Society',food:'Toast, fruit & tea',location:'Library café · example venue',offset:0,hour:8,price:0,signup_required:false,emoji:'🥞',color:'#F1E4CD',description:'An example morning event. Once it has started it becomes grey, and it disappears after midnight in Durham.',dietary:[]}
  ];
  return entries.map(e => ({...e,starts_at:londonTime(addDays(today,e.offset),e.hour,e.minute||0),ends_at:londonTime(addDays(today,e.offset),e.hour+2,e.minute||0),signup_deadline:e.signup_required?londonTime(addDays(today,e.offset-1),20):null,source_url:null,source_label:'Example listing',demo:true,food_is_free:true,eligibility:'Example only — not an event to attend',verified_at:null}));
}
