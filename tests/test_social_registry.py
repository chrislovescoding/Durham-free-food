from datetime import datetime, timezone
import unittest

from scripts.social_registry import apply_registry, expand_accounts


NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)
SOCIETY = {'name': 'Example', 'url': 'https://www.durhamsu.com/groups/studentgroup/1/'}
MAPPING = {'society_url': SOCIETY['url'], 'platform': 'instagram', 'url': 'https://instagram.com/example/',
           'evidence_url': SOCIETY['url'], 'verified_at': '2026-10-16T19:00:00Z', 'reviewed': True}


class RegistryTests(unittest.TestCase):
    def test_reviewed_mapping_survives_missing_discovery_without_mutating_input(self):
        societies, errors = apply_registry([SOCIETY], {'accounts': [MAPPING]}, NOW)
        self.assertFalse(errors)
        self.assertEqual(societies[0]['instagram_url'], 'https://www.instagram.com/example/')
        self.assertNotIn('instagram_url', SOCIETY)
        self.assertEqual(societies[0]['social_accounts'][0]['method'], 'reviewed_registry')

    def test_unverified_invalid_or_removed_society_mapping_is_reported(self):
        for change in ({'reviewed': False}, {'society_url': 'https://example.com/'},
                       {'url': 'https://instagram.com/p/post/'}, {'platform': 'facebook'},
                       {'verified_at': '2027-01-01T00:00:00Z'}, {'evidence_url': 'javascript:bad'}):
            with self.subTest(change=change):
                societies, errors = apply_registry([SOCIETY], {'accounts': [{**MAPPING, **change}]}, NOW)
                self.assertEqual(len(errors), 1)
                self.assertNotIn('instagram_url', societies[0])

    def test_multiaccount_expansion_preserves_singular_and_deduplicates(self):
        result = expand_accounts([{**SOCIETY, 'instagram_url': 'https://instagram.com/first/',
            'social_accounts': [{'platform': 'instagram', 'url': 'https://www.instagram.com/first/'},
                                {'platform': 'instagram', 'url': 'https://instagram.com/second/'},
                                {'platform': 'facebook', 'url': 'https://facebook.com/groups/example/'}]}])
        self.assertEqual(len(result), 3)
        self.assertTrue(any('groups/example' in r.get('facebook_url', '') for r in result))

    def test_reviewed_group_is_preserved_for_unsupported_coverage(self):
        mapping = {**MAPPING, 'platform': 'facebook', 'url': 'https://facebook.com/groups/Example/'}
        societies, errors = apply_registry([SOCIETY], {'accounts': [mapping]}, NOW)
        self.assertFalse(errors)
        self.assertEqual(expand_accounts(societies)[0]['facebook_url'],
                         'https://www.facebook.com/groups/example/')

    def test_group_post_and_foreign_host_cannot_be_registered_as_group(self):
        for url in ('https://facebook.com/groups/example/posts/123/',
                    'https://facebook.com.evil.test/groups/example/',
                    'https://facebook.com/groups/../',
                    'https://facebook.com:9999/groups/example/'):
            with self.subTest(url=url):
                _, errors = apply_registry([SOCIETY], {'accounts': [
                    {**MAPPING, 'platform': 'facebook', 'url': url}]}, NOW)
                self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
