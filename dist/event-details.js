import { eventStatus, formatDate, entryLabel, signupLabel, safeUrl } from './events.js';
import { promotionFor } from './promotions.js';

const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const icons = {
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  pin: '<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2"/>',
  food: '<path d="M5 3v6a3 3 0 0 0 6 0V3M8 3v19m11 0V3c-4 2-4 9 0 10"/>',
  ticket: '<path d="M3 7h18v4a2 2 0 0 0 0 4v4H3v-4a2 2 0 0 0 0-4V7Zm12 0v2m0 3v2m0 3v2"/>',
  people: '<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3m2-16a3 3 0 0 1 0 6m2 10v-3a6 6 0 0 0-2-4"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 11h18"/>',
  arrow: '<path d="M7 17 17 7M7 7h10v10"/>',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${icons[name]}</svg>`;

// Venue aliases resolve rooms to their named building, without guessing a room pin.
// TLC: https://dcad.webspace.durham.ac.uk/2023/06/08/pgr-development-conference-2023/
// SU rooms: https://www.durhamsu.com/resources/final-assembly-papers-december-2022/download_attachment
export function eventMapData(event) {
  const location = String(event.location || '').trim();
  let query = location;
  let note = 'Map search for the venue above.';
  let areaOnly = false;
  if (/^TLC\s*\d+\b/i.test(location)) {
    query = 'Teaching and Learning Centre, Durham University, South Road, Durham, UK';
    note = 'Map shows the Teaching and Learning Centre. Use the room number above.';
  } else if (/\b(student[s’']?\s*union|SU lounge|kingsgate|dunelm house)\b/i.test(location)) {
    query = 'Dunelm House, New Elvet, Durham, UK';
    note = 'Map shows Dunelm House. Use the room or venue name above.';
  } else if (/^grey prayer room$/i.test(location)) {
    query = 'Grey College, South Road, Durham, UK';
    note = 'Map shows Grey College. Check the original post for the prayer-room entrance.';
  } else if (!location || /^(durham(?:,?\s*(?:uk|city centre))?|city centre|campus|tbc|tbd|to be (?:announced|confirmed)|not (?:stated|confirmed)|online)$/i.test(location) || /^(?:room|lecture room|seminar room)\s*[A-Z0-9 -]*$/i.test(location)) {
    query = 'Durham, UK';
    areaOnly = true;
    note = 'Durham area only. The organiser has not given a map-ready venue.';
  } else {
    query += /\bdurham\b/i.test(query) ? ', UK' : ', Durham, UK';
  }
  const params = new URLSearchParams({ q: query, output: 'embed', hl: 'en' });
  const directions = new URL('https://www.google.com/maps/dir/');
  directions.search = new URLSearchParams({ api: '1', destination: query }).toString();
  return {
    embedUrl: `https://www.google.com/maps?${params}`,
    directionsUrl: areaOnly ? null : directions.href,
    title: areaOnly ? 'Map of Durham' : `Map for ${location}`,
    note,
    areaOnly,
  };
}

function fact(label, value, symbol, extra = '') {
  return `<div class="event-detail-fact">${icon(symbol)}<div><dt>${escape(label)}</dt><dd>${escape(value)}${extra}</dd></div></div>`;
}

export function renderEventDetails(event, { now = new Date() } = {}) {
  const status = eventStatus(event, now);
  const finished = ['past', 'ended'].includes(status);
  const source = safeUrl(event.source_url);
  const date = formatDate(event.starts_at, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
  const time = formatDate(event.starts_at, { hour: '2-digit', minute: '2-digit' }) + (event.ends_at ? `–${formatDate(event.ends_at, { hour: '2-digit', minute: '2-digit' })}` : '');
  const map = eventMapData(event);
  const promotion = promotionFor(event, { now });
  const deadline = event.signup_required === true
    ? `<span class="event-fact-note">${event.signup_deadline ? `Sign up by ${escape(formatDate(event.signup_deadline, { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' }))}` : 'Deadline not stated — check the original post.'}</span>`
    : '';
  return `<div class="event-detail-sheet${finished ? ' is-past' : ''}">
    <header class="event-detail-header">
      <div class="event-detail-title-block"><p class="event-detail-society">${escape(event.society)}</p><h2 id="dialog-title">${escape(event.title)}</h2></div>
      <div class="event-detail-date" aria-hidden="true"><span>${formatDate(event.starts_at, { month: 'short' })}</span><strong>${formatDate(event.starts_at, { day: 'numeric' })}</strong><span>${formatDate(event.starts_at, { weekday: 'short' })}</span></div>
    </header>
    ${promotion ? `<p class="sponsor-disclosure"><strong>Sponsored${promotion.demo ? ' · Demo placement' : ''}</strong>${promotion.demo ? 'This real event demonstrates a promoted listing. Its organiser has not sponsored Grubr.' : 'The organiser paid for priority placement. Event details are checked in the same way as other listings.'}</p>` : ''}
    ${finished ? '<p class="event-past-message"><strong>Past event</strong><span>This event has already happened. Here’s what was on offer.</span></p>' : status === 'started' ? '<p class="event-past-message"><strong>Already started</strong><span>Check with the organiser before heading over.</span></p>' : ''}
    <section class="event-food-offer" aria-label="Food on offer">${icon('food')}<div><span>On the menu</span><p>${escape(event.food || 'Free food')}</p></div></section>
    <div class="event-detail-body">
      <section class="event-detail-info" aria-label="Event information">
        ${event.description ? `<p class="event-detail-description">${escape(event.description)}</p>` : ''}
        <dl class="event-detail-facts">
          ${fact('When', date, 'clock', `<span class="event-fact-note">${escape(time)} · UK time</span>`)}
          ${fact('Entry', entryLabel(event.price), 'ticket')}
          ${fact('Sign-up', signupLabel(event, now), 'calendar', deadline)}
          ${fact('Who can come', event.eligibility || 'Not stated — check with the organiser.', 'people')}
        </dl>
        <div class="event-dietary"><span>Dietary options</span><p>${escape(event.dietary?.length ? event.dietary.join(', ') : 'Not stated — ask the organiser.')}</p></div>
      </section>
      <section class="event-location" aria-label="Location and map">
        <div class="event-location-heading">${icon('pin')}<div><h3>Find it here</h3><p>${escape(event.location || 'Venue not confirmed')}</p></div></div>
        <div class="event-map-frame"><iframe src="${escape(map.embedUrl)}" title="${escape(map.title)}" loading="lazy" referrerpolicy="no-referrer-when-downgrade" allowfullscreen></iframe></div>
        <p class="event-map-note">${escape(map.note)}</p>
        ${map.directionsUrl ? `<a class="event-directions" href="${escape(map.directionsUrl)}" target="_blank" rel="noopener noreferrer">Get directions ${icon('arrow')}</a>` : ''}
      </section>
    </div>
    <div class="event-detail-footer">
      <div class="event-detail-actions">${source ? `<a class="primary-button" href="${escape(source)}" target="_blank" rel="noopener noreferrer">View original post ${icon('arrow')}</a>` : ''}<button id="dialog-save" class="secondary-button" type="button">Save event</button><button id="add-calendar" class="secondary-button" type="button" ${finished ? 'disabled' : ''}>${icon('calendar')}${finished ? 'Event finished' : 'Add to calendar'}</button></div>
      <p class="event-source-note">${finished ? 'The original post is kept for reference.' : 'Check the original post for the latest details before you go.'}</p>
    </div>
  </div>`;
}
