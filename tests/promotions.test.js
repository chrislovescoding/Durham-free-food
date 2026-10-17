import test from 'node:test';
import assert from 'node:assert/strict';
import { buildGridItems, promotionFor } from '../dist/promotions.js';
import { filterEvents } from '../dist/events.js';
const now = new Date('2026-10-17T12:00:00Z');
const make = (id, starts_at='2026-10-20T18:00:00Z') => ({ id, starts_at, title:id, society:'Test society', price:0, signup_required:false });
const campaigns = [{ event_id:'promoted', active:true, demo:true }];
test('matching sponsored events move first without duplicating or reordering standard events', () => {
  const events=[make('first'),make('second'),make('promoted'),make('last')];
  const items=buildGridItems(events,{now,campaigns});
  assert.deepEqual(items.filter(i=>i.kind==='event').map(i=>i.event.id),['promoted','first','second','last']);
  assert.equal(items[0].promotion.demo,true);
  assert.equal(items[2].kind,'advert');
  assert.equal(items.filter(i=>i.kind==='event').length,events.length);
});
test('past, started, expired and inactive campaigns never receive priority', () => {
  assert.equal(promotionFor(make('promoted','2026-10-16T18:00:00Z'),{now,campaigns}),null);
  assert.equal(promotionFor(make('promoted','2026-10-17T10:00:00Z'),{now,campaigns}),null);
  for(const change of [{active:false},{active_until:'2026-10-16T00:00:00Z'},{active_from:'2026-10-18T00:00:00Z'}]) {
    assert.equal(promotionFor(make('promoted'),{now,campaigns:[{...campaigns[0],...change}]}),null);
  }
});
test('filtered-out sponsored events stay excluded; ad slots can be disabled and never stand alone', () => {
  const filtered=filterEvents([make('standard'),make('promoted')],{search:'standard'},now);
  assert.deepEqual(buildGridItems(filtered,{now,campaigns}).map(i=>i.event.id),['standard']);
  assert.deepEqual(buildGridItems([],{now,campaigns}),[]);
  const events=Array.from({length:10},(_,i)=>make(String(i)));
  assert.equal(buildGridItems(events,{now,campaigns}).filter(i=>i.kind==='advert').length,2);
  assert.equal(buildGridItems(events,{now,campaigns,adsEnabled:false}).length,10);
});
