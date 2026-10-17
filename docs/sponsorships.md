# Sponsorship prototype

`dist/promotions.js` holds trusted campaign configuration separately from collected event facts. The Italian Society event is a real source-backed event used to demonstrate priority placement. Its organiser has not paid for sponsorship; the ribbon, details and partner dialog disclose this.

The renderer filters events first, then moves matching upcoming sponsored events above standard listings. Standard results retain their chronological order. Started/past events and inactive, not-yet-active or expired campaigns receive no boost. Campaigns can set `active_from` and `active_until` as ISO timestamps.

Two example advert creatives appear after the second and eighth event when another result follows. Ads are excluded from event totals and are hidden in saved-only results, text searches and the past-events view. They are clearly marked as demonstration advertisements.

The partner dialog explains promoted events and grid advertising. It does not sell placements, collect payment or imply live advertiser relationships. A future commercial launch needs confirmed campaigns and advertiser destinations before replacing these examples.

Run `npm test` for ranking, expiry, filtering and count checks. `npm run build` embeds the promotions module and stylesheet in the Sites Worker.
