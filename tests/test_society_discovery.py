"""Account discovery coverage and external-fetch safety contracts."""

import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch


SPEC = importlib.util.spec_from_file_location("society_discovery", Path(__file__).parents[1] / "scripts/society_discovery.py")
discovery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(discovery)
SU = "https://www.durhamsu.com/groups/studentgroup/6057/"


class AccountDiscoveryTests(unittest.TestCase):
    def test_plain_linktree_url_and_multiple_accounts_preserve_evidence(self):
        fetch = Mock(return_value='<a href="https://instagram.com/duastrosoc">IG</a><a href="https://instagram.com/astro_events">Events IG</a><a href="https://facebook.com/groups/DUastrosoc">FB</a>')
        result = discovery.discover_accounts('<p>Find links to all of social media accounts here: https://linktr.ee/duastrosoc</p>', SU, fetch)
        fetch.assert_called_once_with('https://linktr.ee/duastrosoc')
        self.assertEqual(len(result['social_accounts']), 3)
        self.assertEqual(result['instagram_url'], 'https://www.instagram.com/duastrosoc/')
        self.assertEqual(result['discovery_status'], 'found')
        self.assertTrue(all(r['evidence_url'] == 'https://linktr.ee/duastrosoc' for r in result['social_accounts']))

    def test_explicit_instagram_handle_is_not_an_email_or_bare_mention(self):
        result = discovery.discover_accounts('<p>Contact Instagram @ais_durham! Email team@durham.ac.uk. Thanks @another_soc.</p>', SU, Mock())
        self.assertEqual([a['url'] for a in result['social_accounts']], ['https://www.instagram.com/ais_durham/'])
        self.assertEqual(result['social_accounts'][0]['method'], 'labelled_handle')

    def test_sentence_period_is_not_part_of_labelled_handle(self):
        result = discovery.discover_accounts('<p>Instagram @ais_durham.</p>', SU, Mock())
        self.assertEqual(result['instagram_url'], 'https://www.instagram.com/ais_durham/')

    def test_unlinked_social_urls_and_old_http_links_are_normalized(self):
        result = discovery.discover_accounts('<p>https://instagram.com/AstroSoc/ and http://www.facebook.com/AstroSoc?ref=foo</p>', SU, Mock())
        self.assertEqual(result['instagram_url'], 'https://www.instagram.com/astrosoc/')
        self.assertEqual(result['facebook_url'], 'https://www.facebook.com/AstroSoc/')

    def test_su_chrome_removed_but_external_society_footer_kept(self):
        html = '<header><a href="https://instagram.com/suheader">bad</a></header><a href="https://finance.example.org/">Our website</a><footer><a href="https://facebook.com/durhamsu">SU</a></footer>'
        fetch = Mock(return_value='<footer><a href="https://instagram.com/dufsofficial">IG</a><a href="https://facebook.com/groups/durhamfinancesociety">FB</a></footer>')
        result = discovery.discover_accounts(html, SU, fetch)
        self.assertEqual(result['instagram_url'], 'https://www.instagram.com/dufsofficial/')
        self.assertEqual(len(result['social_accounts']), 2)

    def test_direct_both_platforms_avoid_external_request(self):
        fetch = Mock()
        result = discovery.discover_accounts('<a href="https://instagram.com/soc">IG</a><a href="https://facebook.com/soc">FB</a><a href="https://soc.example.org/">Website</a>', SU, fetch)
        fetch.assert_not_called()
        self.assertEqual(result['discovery_status'], 'found')

    def test_one_hop_and_two_page_request_limit(self):
        fetch = Mock(return_value='<a href="https://third.example.org/">Our website</a>')
        result = discovery.discover_accounts(' '.join(f'<a href="https://site{i}.example.org/">Our website</a>' for i in range(4)), SU, fetch)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(result['discovery_status'], 'not_listed')
        self.assertNotIn('https://third.example.org/', result['discovery_pages_checked'])

    def test_request_failure_is_distinct_from_missing_links(self):
        fetch = Mock(side_effect=RuntimeError('404 missing page'))
        result = discovery.discover_accounts('<a href="https://linktr.ee/expired">Our Linktree</a>', SU, fetch)
        self.assertEqual(result['discovery_status'], 'error')
        self.assertEqual(len(result['discovery_errors']), 1)
        partial = discovery.discover_accounts('<p>Instagram @known_soc</p><a href="https://linktr.ee/expired">Our Linktree</a>', SU, fetch)
        self.assertEqual(partial['discovery_status'], 'partial')
        self.assertEqual(partial['instagram_url'], 'https://www.instagram.com/known_soc/')

    def test_unsafe_and_unrelated_destinations_are_not_fetched(self):
        urls = ['http://example.org/', 'https://127.0.0.1/', 'https://10.0.0.1/', 'https://localhost/', 'https://host.local/', 'https://host.internal/', 'https://user:password@example.org/', 'https://example.org:8443/', 'javascript:alert(1)', 'mailto:team@example.org', 'https://eventbrite.com/', 'https://example.org/news/article', 'https://example.org/tickets', 'https://example.org/?token=secret', 'https://www.ukmsl.com/']
        fetch = Mock()
        result = discovery.discover_accounts(' '.join(f'<a href="{u}">Website</a>' for u in urls), SU, fetch)
        fetch.assert_not_called()
        self.assertEqual(result['social_accounts'], [])

    def test_posts_shares_login_and_hidden_handles_are_not_accounts(self):
        html = '<a href="https://instagram.com/p/POST">Post</a><a href="https://facebook.com/soc/posts/123">Post</a><a href="https://facebook.com/login">Login</a><a href="https://facebook.com/sharer.php?u=x">Share</a><script>Instagram @script_account</script><p hidden>Instagram @hidden_account</p><p>Instagram @durhamsurvivors</p>'
        result = discovery.discover_accounts(html, SU, Mock())
        self.assertEqual([a['url'] for a in result['social_accounts']], ['https://www.instagram.com/durhamsurvivors/'])

    def test_sponsor_homepage_never_promotes_its_owned_profile_to_society(self):
        sponsor_html = '<footer><a href="https://instagram.com/the_sponsor/">Instagram</a></footer>'
        fetch = Mock(return_value=sponsor_html)
        result = discovery.discover_accounts('<p><a href="https://sponsor.example.org/">Our sponsor</a></p>', SU, fetch)
        fetch.assert_not_called()
        self.assertEqual(result['social_accounts'], [])
        self.assertEqual(len(result['discovery_review_links']), 1)

    def test_bare_homepage_and_linktree_need_affirmative_ownership(self):
        fetch = Mock(return_value='<a href="https://instagram.com/unrelated">Instagram</a>')
        result = discovery.discover_accounts('<a href="https://organisation.example.org/">Website</a><p>https://linktr.ee/unrelated</p>', SU, fetch)
        fetch.assert_not_called()
        self.assertEqual(len(result['discovery_review_links']), 2)
        self.assertEqual(result['social_accounts'], [])

    def test_partner_and_careers_cues_override_website_wording(self):
        for cue in ('Our sponsor website', 'Our partner: official website', 'Our careers website', 'Parent organisation: official website'):
            fetch = Mock()
            result = discovery.discover_accounts(f'<a href="https://other.example.org/">{cue}</a>', SU, fetch)
            fetch.assert_not_called()
            self.assertEqual(result['social_accounts'], [])

    def test_su_dedicated_website_field_preserves_computing_and_finance_flow(self):
        fetch = Mock(return_value='<footer><a href="https://instagram.com/ducompsoc">Instagram</a><a href="https://facebook.com/DurhamCompSoc">Facebook</a></footer>')
        html = '<div class="socialmedia-c"><div class="mslwidget"><a class="website" href="https://compsoc.tech/">compsoc.tech/</a></div></div>'
        result = discovery.discover_accounts(html, SU, fetch)
        fetch.assert_called_once_with('https://compsoc.tech/')
        self.assertEqual(result['instagram_url'], 'https://www.instagram.com/ducompsoc/')
        self.assertEqual(result['discovery_review_links'], [])

    def test_owned_site_sponsor_social_is_not_attributed_to_society(self):
        fetch = Mock(return_value='<div class="sponsors"><a href="https://instagram.com/sponsor">Instagram</a></div><footer><a href="https://instagram.com/real_society">Instagram</a></footer>')
        result = discovery.discover_accounts('<a href="https://society.example.org/">Our website</a>', SU, fetch)
        self.assertEqual([a['url'] for a in result['social_accounts']], ['https://www.instagram.com/real_society/'])


class SafeFetchTests(unittest.TestCase):
    def _response(self, status=200, headers=None, chunks=(b'<html>public</html>',)):
        response = Mock(status_code=status, headers=headers or {}, encoding='utf-8')
        response.iter_content.return_value = iter(chunks)
        return response

    def _session(self, responses):
        session = Mock()
        session.__enter__ = Mock(return_value=session)
        session.__exit__ = Mock(return_value=False)
        session.get.side_effect = responses
        return session

    @patch.object(discovery.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    def test_redirect_to_localhost_rejected_before_second_request(self, dns):
        session = self._session([self._response(302, {'Location': 'https://127.0.0.1/private'})])
        with patch.object(discovery.requests, 'Session', return_value=session):
            with self.assertRaisesRegex(ValueError, 'public HTTPS'):
                discovery.safe_discovery_fetch('https://soc.example.org/')
        self.assertEqual(session.get.call_count, 1)

    @patch.object(discovery.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    def test_redirect_to_login_or_social_site_is_not_followed(self, dns):
        for target in ('https://next.example.org/login', 'https://www.facebook.com/society'):
            session = self._session([self._response(302, {'Location': target})])
            with patch.object(discovery.requests, 'Session', return_value=session):
                with self.assertRaisesRegex(ValueError, 'public society website'):
                    discovery.safe_discovery_fetch('https://soc.example.org/')
            self.assertEqual(session.get.call_count, 1)

    @patch.object(discovery.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.2', 443))])
    def test_hostname_resolving_private_is_rejected_before_request(self, dns):
        session = self._session([])
        with patch.object(discovery.requests, 'Session', return_value=session):
            with self.assertRaisesRegex(ValueError, 'private or reserved'):
                discovery.safe_discovery_fetch('https://soc.example.org/')
        session.get.assert_not_called()

    @patch.object(discovery.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    def test_redirects_revalidated_and_bounded(self, dns):
        session = self._session([self._response(302, {'Location': 'https://next.example.org/'}) for _ in range(4)])
        with patch.object(discovery.requests, 'Session', return_value=session):
            with self.assertRaisesRegex(ValueError, 'redirect limit'):
                discovery.safe_discovery_fetch('https://soc.example.org/')
        self.assertEqual(session.get.call_count, 4)
        self.assertEqual(dns.call_count, 4)

    @patch.object(discovery.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    def test_stream_limit_stops_oversized_source(self, dns):
        session = self._session([self._response(chunks=(b'x' * 1_000_001, b'x' * 1_000_001))])
        with patch.object(discovery.requests, 'Session', return_value=session):
            with self.assertRaisesRegex(ValueError, 'size or time'):
                discovery.safe_discovery_fetch('https://soc.example.org/')

    @patch.object(discovery.socket, 'getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))])
    def test_public_html_is_returned_without_ambient_credentials(self, dns):
        session = self._session([self._response(headers={'Content-Type': 'text/html; charset=utf-8'})])
        with patch.object(discovery.requests, 'Session', return_value=session):
            self.assertEqual(discovery.safe_discovery_fetch('https://soc.example.org/'), '<html>public</html>')
        self.assertFalse(session.trust_env)
        self.assertEqual(session.get.call_args.kwargs['timeout'], (5, 10))
        self.assertFalse(session.get.call_args.kwargs['allow_redirects'])


if __name__ == '__main__':
    unittest.main()
