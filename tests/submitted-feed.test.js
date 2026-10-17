import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeEventFeeds } from '../dist/events.js';

const event = { id:'reviewed', title:'Build Day', society:'Entrepreneurs Durham', starts_at:'2026-10-17T11:00:00+01:00', source_url:'https://www.instagram.com/p/DeEVClHESd9/', food_is_free:true, price:0 };

test('submitted events preserve reviewed listings across canonical URL and timezone variants', () => {
  const duplicate = {...event,id:'ai-copy',source_url:'https://instagram.com/reel/DeEVClHESd9/?img_index=2',starts_at:'2026-10-17T10:00:00Z',price:null};
  const nextDate = {...event,id:'next-date',starts_at:'2026-10-24T11:00:00+01:00'};
  assert.deepEqual(mergeEventFeeds([event],[duplicate,nextDate]),[event,nextDate]);
});

test('unverified or malformed feed records cannot appear as submitted events', () => {
  const other = {...event,id:'new',source_url:'https://www.instagram.com/p/AnotherPost/'};
  assert.deepEqual(mergeEventFeeds([event],[null,{}, {...other,food_is_free:false},{...other,starts_at:'not-a-date'},{...other,source_url:'javascript:alert(1)'},other]),[event,other]);
});
