import test from 'node:test';
import assert from 'node:assert/strict';
import {dayKey,eventStatus,filterEvents,signupLabel,calendarFile,safeUrl,isValidEvent,demoEvents} from '../dist/events.js';
const now=new Date('2026-10-17T11:00:00Z');
const base={id:'real',title:'Pizza social',society:'Test society',starts_at:'2026-10-17T18:00:00+01:00',ends_at:'2026-10-17T20:00:00+01:00',price:0,food_is_free:true,food:'Pizza',location:'Durham',source_url:'https://example.org/event',signup_required:false};
test('London midnight moves yesterday into history even when UTC is still yesterday',()=>{const moment=new Date('2026-06-17T23:30:00Z');const event={...base,starts_at:'2026-06-17T20:00:00+01:00',ends_at:'2026-06-17T22:00:00+01:00'};assert.equal(dayKey(moment),'2026-06-18');assert.equal(eventStatus(event,moment),'past');assert.equal(filterEvents([event],{date:'upcoming'},moment).length,0);assert.equal(filterEvents([event],{date:'recent'},moment).length,1);});
test('started and ended events stay visible on the same London day',()=>{const started={...base,starts_at:'2026-10-17T10:00:00+01:00',ends_at:'2026-10-17T11:00:00+01:00'};assert.equal(eventStatus(started,now),'ended');assert.equal(filterEvents([started],{},now).length,1);});
test('autumn DST day uses London calendar date rather than a fixed offset',()=>{assert.equal(dayKey('2026-10-25T00:30:00Z'),'2026-10-25');assert.equal(dayKey('2026-10-25T23:30:00Z'),'2026-10-25');});
test('unknown cost and unknown sign-up never pass free/no-sign-up filters',()=>{const unknown={...base,id:'unknown',price:null,signup_required:null};assert.deepEqual(filterEvents([base,unknown],{freeEntry:true},now).map(e=>e.id),['real']);assert.deepEqual(filterEvents([base,unknown],{signup:'none'},now).map(e=>e.id),['real']);});
test('date, text and saved filters combine and earliest appears first',()=>{const tomorrow={...base,id:'tomorrow',starts_at:'2026-10-18T12:00:00+01:00'};assert.deepEqual(filterEvents([tomorrow,base],{search:'pizza',savedOnly:true,savedIds:['tomorrow'],date:'tomorrow'},now).map(e=>e.id),['tomorrow']);assert.equal(filterEvents([tomorrow,base],{},now)[0].id,'real');});
test('required registration with expired deadline is closed',()=>{assert.equal(signupLabel({...base,signup_required:true,signup_deadline:'2026-10-17T10:00:00Z'},now),'Sign-up closed');});
test('only explicit free-food source-backed events validate',()=>{assert.ok(isValidEvent(base));assert.ok(!isValidEvent({...base,food_is_free:null}));assert.ok(!isValidEvent({...base,source_url:'javascript:alert(1)'}));assert.ok(!isValidEvent({...base,starts_at:'2026-10-17T18:00:00'}));assert.equal(safeUrl('data:text/html,test'),'');});
test('calendar escapes source text, uses UTC, and marks demonstration events',()=>{const ics=calendarFile({...base,title:'Pizza; chat, friends',description:'Hello\nWorld',demo:true},now);assert.match(ics,/DTSTART:20261017T170000Z/);assert.match(ics,/\[DEMO — not a real event\]/);assert.match(ics,/Pizza\\; chat\\, friends/);assert.match(ics,/Hello\\nWorld/);assert.match(ics,/END:VCALENDAR\r\n$/);});
test('demo events have no fabricated source links and are explicitly labelled',()=>{const examples=demoEvents(now);assert.equal(examples.length,6);assert.ok(examples.every(event=>event.demo===true&&event.source_url===null));assert.ok(examples.some(event=>event.price>0));});

test('recent view keeps fourteen previous London dates and every future date',()=>{
  const boundary={...base,id:'boundary',starts_at:'2026-10-02T23:00:00Z',ends_at:null};
  const older={...base,id:'older',starts_at:'2026-10-02T22:59:00Z',ends_at:null};
  const distant={...base,id:'distant',starts_at:'2026-12-11T18:30:00Z',ends_at:null};
  const events=[older,boundary,distant,base];
  assert.deepEqual(filterEvents(events,{},now).map(e=>e.id),['real','distant','boundary']);
  assert.deepEqual(filterEvents(events,{date:'all'},now).map(e=>e.id),['real','distant','boundary','older']);
});

test('past is newest first and upcoming excludes ended events, including today',()=>{
  const ended={...base,id:'ended',starts_at:'2026-10-17T08:00:00Z',ends_at:'2026-10-17T09:00:00Z'};
  const ongoing={...base,id:'ongoing',starts_at:'2026-10-17T10:00:00Z',ends_at:'2026-10-17T12:00:00Z'};
  const past={...base,id:'past',starts_at:'2026-10-08T18:30:00Z',ends_at:null};
  const events=[past,ended,base,ongoing];
  assert.deepEqual(filterEvents(events,{date:'past'},now).map(e=>e.id),['ended','past']);
  assert.deepEqual(filterEvents(events,{date:'upcoming'},now).map(e=>e.id),['ongoing','real']);
  assert.deepEqual(filterEvents(events,{date:'week'},now).map(e=>e.id),['ongoing','real','ended']);
  assert.equal(signupLabel(past,now),'Event finished');
  assert.equal(signupLabel(ended,now),'Event finished');
});

test('historical events still support search, saved and entry filters',()=>{
  const past={...base,id:'past',starts_at:'2026-09-30T18:30:00Z',ends_at:null};
  assert.deepEqual(filterEvents([past,base],{date:'all',search:'pizza',savedOnly:true,savedIds:['past'],freeEntry:true},now).map(e=>e.id),['past']);
  assert.equal(filterEvents([past],{date:'today'},now).length,0);
  assert.equal(filterEvents([past],{date:'week'},now).length,0);
});
