from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

def module(name):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'scripts' / (name + '.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result

parser = module('social_events')
refresh = module('refresh')
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)

class SocialEventsTests(unittest.TestCase):
    def post(self, text):
        return {'id':'ig-abc', 'platform':'instagram', 'society':'Test Society', 'source_url':'https://www.instagram.com/p/abc/', 'text':text, 'published_at':'2026-10-16T12:00:00Z', 'observed_at':'2026-10-17T12:00:00Z', 'origin':'public_html'}

    def extract(self, text):
        return parser.posts_to_events([self.post(text)], NOW, refresh.food_evidence)

    def test_real_caption_fields_are_distinct_from_post_date(self):
        events, queue = self.extract('Welcome social\nFree pizza! Sunday 18 October 2026 at 6pm\nLocation: Kingsgate Cafe\nFree entry. Just turn up.')
        self.assertFalse(queue)
        event=events[0]
        self.assertEqual(event['starts_at'],'2026-10-18T18:00:00+01:00')
        self.assertEqual(event['price'],0)
        self.assertFalse(event['signup_required'])
        self.assertEqual(event['location'],'Kingsgate Cafe')

    def test_unknown_price_and_signup_stay_unknown(self):
        events,_=self.extract('Free pizza! 18 October 2026 at 18:30\nVenue: Room 1')
        self.assertIsNone(events[0]['price']);self.assertIsNone(events[0]['signup_required'])

    def test_relative_date_anchors_to_post_not_crawl(self):
        events,_=self.extract('Free pizza tomorrow at 6pm\n📍 Student room')
        self.assertEqual(events[0]['starts_at'],'2026-10-17T18:00:00+01:00')

    def test_old_posts_do_not_resurface_as_new_events(self):
        post=self.post('Free pizza tomorrow at 6pm\nLocation: Durham')
        post['published_at']='2025-10-16T12:00:00Z'
        self.assertEqual(parser.posts_to_events([post],NOW,refresh.food_evidence),([],[]))

    def test_yearless_dates_require_known_publication(self):
        post=self.post('Free pizza 18 October at 6pm\nLocation: Durham');post['published_at']=None
        events,queue=parser.posts_to_events([post],NOW,refresh.food_evidence)
        self.assertFalse(events);self.assertEqual(len(queue),1)

    def test_ambiguous_caption_and_image_only_details_need_review(self):
        for text in ['Free pizza 18 October 2026 at 6pm', 'Free pizza 18 October 2026 at 6pm and 8pm\nLocation: Durham', 'Free pizza Monday 18 October 2026 at 6pm\nLocation: Durham', 'Free pizza 18 October 2026 at 6pm. Register by 17 October\nLocation: Durham', 'Free pizza 25 October 2026 at 1:30am\nLocation: Durham']:
            with self.subTest(text=text):
                events,queue=self.extract(text);self.assertFalse(events);self.assertTrue(queue)

    def test_ticket_release_and_booking_times_are_not_event_start_times(self):
        for timing in [
            'Tickets go live today at 6pm.',
            'Tickets go on sale at 6pm.',
            'Tickets will be released at 6pm.',
            'Booking closes tomorrow at 6pm.',
            'Registration opens at 6pm.',
            'Sign-ups close at 6pm.',
            'RSVP before 6pm.',
            'Opening time for bookings: 6pm.',
        ]:
            with self.subTest(timing=timing):
                events, queue = self.extract('Free pizza social on 18 October 2026!\n' + timing + '\nLocation: Durham')
                self.assertFalse(events)
                self.assertEqual(len(queue), 1)
                self.assertIn('registration timing', queue[0]['reason'])
        events, queue = self.extract('Free pizza social! Booking closes tomorrow at 6pm.\nLocation: Durham')
        self.assertFalse(events)
        self.assertEqual(len(queue), 1)

    def test_mixed_explicit_and_relative_dates_are_not_silently_ignored(self):
        for relative in ('today', 'tomorrow', 'tonight', 'next week', 'next Monday'):
            with self.subTest(relative=relative):
                events, queue = self.extract('Free pizza social on 18 October 2026!\nMore information ' + relative + ' at 6pm.\nLocation: Durham')
                self.assertFalse(events)
                self.assertEqual(len(queue), 1)
                self.assertIn('Mixed explicit and relative', queue[0]['reason'])

    def test_compound_relative_date_is_not_mistaken_for_tomorrow(self):
        events, queue = self.extract('Free pizza the day after tomorrow at 6pm\nLocation: Durham')
        self.assertFalse(events)
        self.assertEqual(len(queue), 1)

    def test_conditional_and_tiered_free_entry_needs_review(self):
        for offer in [
            'Free entry for members; £3 for non-members.',
            'Free admission. Membership required.',
            'Free entry before 7pm.',
            'Free entry with a student card.',
            'Free entry for Durham students.',
            'Free entry for the first 20 arrivals.',
            'Free entry. Advance tickets £2.',
            'Free entry. Standard tickets 3 GBP.',
            'Not free entry.',
            'Free entry is not available.',
        ]:
            with self.subTest(offer=offer):
                events, queue = self.extract('Free pizza! 18 October 2026 at 6pm\nLocation: Durham\n' + offer)
                self.assertFalse(events)
                self.assertEqual(len(queue), 1)

    def test_unconditional_free_entry_and_paid_entry_remain_supported(self):
        for offer, price in [('Free entry.', 0), ('Free admission for everyone.', 0), ('£3 entry.', 3)]:
            with self.subTest(offer=offer):
                events, queue = self.extract('Free pizza! 18 October 2026 at 6pm\nLocation: Durham\n' + offer)
                self.assertFalse(queue)
                self.assertEqual(events[0]['price'], price)

    def test_paid_entry_and_negated_food(self):
        events,_=self.extract('Free pizza 18 October 2026 at 6pm\nLocation: Durham\n£3 entry. Registration required.')
        self.assertEqual(events[0]['price'],3);self.assertTrue(events[0]['signup_required'])
        events,queue=self.extract('No longer free pizza. 18 October 2026 at 6pm\nLocation: Durham')
        self.assertFalse(events);self.assertTrue(queue)

    def test_image_only_free_food_is_attributed_and_needs_review(self):
        post = self.post('Our next society social!')
        post.update(origin='apify', media_urls=['https://example.org/poster.jpg'], image_ocr={
            'status': 'ok', 'coverage_complete': True, 'expected_images': 1, 'inspected_images': 1,
            'text': 'Free pizza 18 October 2026 at 6pm\nLocation: Durham SU',
            'images': [{'image_id': 'poster', 'status': 'ok', 'text': 'Free pizza'}]})
        events, queue = parser.posts_to_events([post], NOW, refresh.food_evidence)
        self.assertFalse(events)
        self.assertEqual(len(queue), 1)
        self.assertIsNone(queue[0]['evidence']['caption']['free_food'])
        self.assertEqual(queue[0]['evidence']['image_ocr']['free_food'], 'Free pizza')
        self.assertTrue(queue[0]['needs_visual_review'])

    def test_caption_and_poster_conflict_never_auto_publishes(self):
        post = self.post('Free pizza 18 October 2026 at 6pm\nLocation: Durham SU')
        post['image_ocr'] = {'status': 'ok', 'coverage_complete': True, 'text': 'CANCELLED. No free pizza.'}
        events, queue = parser.posts_to_events([post], NOW, refresh.food_evidence)
        self.assertFalse(events)
        self.assertIn('review together', queue[0]['reason'])
        self.assertEqual(queue[0]['evidence']['caption']['free_food'], 'Free pizza')
        self.assertIsNone(queue[0]['evidence']['image_ocr']['free_food'])

    def test_missing_or_unreadable_images_are_not_silently_dropped(self):
        for image in (None, {'status': 'failed', 'coverage_complete': False, 'text': ''},
                      {'status': 'ok', 'coverage_complete': True, 'text': ''},
                      {'status': 'partial', 'coverage_complete': False, 'text': 'Society social'}):
            with self.subTest(image=image):
                post = {**self.post(''), 'origin': 'apify', 'image_ocr': image}
                events, queue = parser.posts_to_events([post], NOW, refresh.food_evidence)
                self.assertFalse(events)
                self.assertEqual(len(queue), 1)
                self.assertIn('inspect the original images', queue[0]['reason'])
                self.assertEqual(queue[0]['title'], 'Society social' if image and image.get('text') else 'Society post')

    def test_successful_non_food_ocr_still_requires_visual_review(self):
        post = {**self.post('Committee elections'), 'origin': 'apify',
                'image_ocr': {'status': 'ok', 'coverage_complete': True, 'text': 'Voting opens next week'}}
        events, queue = parser.posts_to_events([post], NOW, refresh.food_evidence)
        self.assertFalse(events)
        self.assertEqual(len(queue), 1)
        self.assertTrue(queue[0]['needs_visual_review'])

    def test_chinese_offer_is_not_dropped_after_english_ocr_completes(self):
        caption = '欢迎新朋友！第一次来的朋友可以免费享用晚餐。'
        post = {**self.post(caption), 'origin': 'apify',
                'image_ocr': {'status': 'ok', 'coverage_complete': True, 'text': 'TERM PLAN 2026'}}
        events, queue = parser.posts_to_events([post], NOW, refresh.food_evidence)
        self.assertFalse(events)
        self.assertEqual(len(queue), 1)
        self.assertEqual(queue[0]['evidence']['caption']['text'], caption)
        self.assertTrue(queue[0]['needs_visual_review'])

    def test_apify_caption_without_ocr_still_requires_image_review(self):
        post = {**self.post('Free pizza 18 October 2026 at 6pm\nLocation: Durham SU'), 'origin': 'apify'}
        events, queue = parser.posts_to_events([post], NOW, refresh.food_evidence)
        self.assertFalse(events)
        self.assertEqual(queue[0]['evidence']['image_ocr']['status'], 'not_processed')

if __name__=='__main__': unittest.main()
