import test from 'node:test';
import assert from 'node:assert/strict';
import { eventMapData, renderEventDetails } from '../dist/event-details.js';

const now = new Date('2026-10-17T12:00:00Z');
const event = {
  id: 'test', title: 'Welcome dinner', society: 'Test Society', food: 'Free dinner for first-time visitors',
  starts_at: '2026-10-23T18:30:00+01:00', ends_at: '2026-10-23T20:00:00+01:00',
  location: 'Elvet Methodist Church, 8 Old Elvet, Durham, DH1 3HL', price: null,
  signup_required: true, signup_deadline: '2026-10-22T18:00:00+01:00',
  eligibility: 'First visit free; returning visitors pay £4.', dietary: [],
  source_url: 'https://www.instagram.com/p/example/', source_label: 'Instagram · AI extracted',
};

test('details keep entry, eligibility and sign-up limits alongside a real map', () => {
  const html = renderEventDetails(event, { now });
  assert.match(html, /Entry price unconfirmed/);
  assert.match(html, /First visit free; returning visitors pay £4\./);
  assert.match(html, /Sign up by 22 Oct/);
  assert.match(html, /<iframe src="https:\/\/www.google.com\/maps\?/);
  assert.match(html, /title="Map for Elvet Methodist Church/);
  assert.match(html, /loading="lazy"/);
  assert.doesNotMatch(html, /AI extracted/);
  assert.doesNotMatch(html, /id="add-calendar"[^>]*disabled/);
});

test('past details preserve original offer but prevent calendar downloads', () => {
  const html = renderEventDetails({ ...event, starts_at: '2026-10-08T18:30:00+01:00', ends_at: '2026-10-08T20:00:00+01:00' }, { now });
  assert.match(html, /Past event/);
  assert.match(html, /id="add-calendar"[^>]*disabled/);
  assert.match(html, /Event finished/);
  assert.match(html, /id="dialog-save"/);
  assert.match(html, /View original post/);
});

test('room aliases map to a verified building, while vague locations do not claim a venue pin', () => {
  const tlc = eventMapData({ location: 'TLC113' });
  assert.match(new URL(tlc.embedUrl).searchParams.get('q'), /Teaching and Learning Centre/);
  assert.match(tlc.note, /room number/);
  const su = eventMapData({ location: 'Kingsgate, Student Union' });
  assert.match(new URL(su.embedUrl).searchParams.get('q'), /Dunelm House/);
  const vague = eventMapData({ location: 'Room 123' });
  assert.equal(vague.areaOnly, true);
  assert.equal(vague.directionsUrl, null);
  assert.equal(new URL(vague.embedUrl).searchParams.get('q'), 'Durham, UK');
});

test('source and event fields cannot inject markup or executable URLs into details', () => {
  const html = renderEventDetails({ ...event, title: '<img src=x onerror=alert(1)>', location: '"><script>bad()</script>', source_url: 'javascript:alert(1)' }, { now });
  assert.doesNotMatch(html, /<img|<script|javascript:/);
  assert.match(html, /&lt;img/);
  assert.match(html, /&quot;&gt;&lt;script&gt;/);
});
