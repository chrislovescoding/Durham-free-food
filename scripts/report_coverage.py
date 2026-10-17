"""Write a concise, credential-free GitHub Actions collection summary."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
social = json.loads((root / 'dist/data/social-posts.json').read_text(encoding='utf-8'))
summary = social.get('coverage', {}).get('summary', {})
print('## Society post collection\n')
for title, key in [('Societies in directory', 'total_societies'),
                   ('Societies with recently observed posts (limited coverage)', 'societies_with_recent_posts'),
                   ('Societies needing account links', 'societies_needing_accounts'),
                   ('Societies needing collection attention', 'societies_needing_attention'),
                   ('Societies with stale collection', 'societies_stale'),
                   ('Unique posts stored', 'post_count')]:
    print(f'- {title}: {summary.get(key, 0)}')
for platform in ('instagram', 'facebook'):
    print(f'- {platform.capitalize()} posts: {sum(p.get("platform") == platform for p in social.get("posts", []))}')
print('\nSee `dist/data/social-posts.json` for every society and account status. '
      'A successful job means the collector ran; it does not mean all accounts were accessible.')
