import { eventStatus } from './events.js';

// Prototype placements only. This real event's organiser has not bought a campaign.
// Keep promotion configuration separate from the collector's factual event data.
export const SPONSORSHIPS = [
  { event_id: 'ai-instagram-12b834dbb368ac3af9', demo: true, active: true },
];
export const ADVERTS = [
  { id: 'local-business', theme: 'orange', audience: 'For local businesses', title: 'Your next regulars are right here.', description: 'Put your café, restaurant or shop in front of Durham students making their next plan.', action: 'Advertise with Grubr' },
  { id: 'society-event', theme: 'green', audience: 'For societies & organisers', title: 'Good events deserve a full table.', description: 'Give your next free-food event a little extra attention with a sponsored spot.', action: 'Explore sponsorship' },
];
export function promotionFor(event, { now = new Date(), campaigns = SPONSORSHIPS } = {}) {
  if (eventStatus(event, now) !== 'upcoming') return null;
  return campaigns.find(campaign => campaign.active === true && campaign.event_id === event.id
    && (!campaign.active_from || Date.parse(campaign.active_from) <= +now)
    && (!campaign.active_until || Date.parse(campaign.active_until) > +now)) || null;
}
export function buildGridItems(events, { now = new Date(), campaigns = SPONSORSHIPS, adsEnabled = true } = {}) {
  // Input is already filtered and ordered by event date; preserve that order
  // within each group. Promotion never brings an excluded event back into view.
  const ranked = events.map(event => ({ kind: 'event', event, promotion: promotionFor(event, { now, campaigns }) }))
    .sort((a, b) => Number(Boolean(b.promotion)) - Number(Boolean(a.promotion)));
  const items = [];
  ranked.forEach((item, index) => {
    items.push(item);
    const slot = index === 1 ? 0 : index === 7 ? 1 : -1;
    if (adsEnabled && slot >= 0 && index < ranked.length - 1) items.push({ kind: 'advert', advert: ADVERTS[slot] });
  });
  return items;
}
