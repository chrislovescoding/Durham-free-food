"""Build an offline caption-and-poster review page from the saved pilot artifact."""
import argparse
from html import escape
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def build(folder):
    folder = Path(folder)
    payload = json.loads((folder / 'posts-with-images.json').read_text(encoding='utf-8'))
    receipt = json.loads((folder / 'run.json').read_text(encoding='utf-8'))
    cache = {path.stem: path.relative_to(folder).as_posix() for path in (folder / 'image-cache').rglob('*.png')}
    cards = []
    for index, post in enumerate(payload['posts'], 1):
        ocr = post.get('image_ocr', {})
        pictures = []
        for item in ocr.get('images', []):
            path = cache.get(item.get('image_id'))
            if path:
                pictures.append(f'<figure><img loading="lazy" src="{escape(path, quote=True)}" alt="Original organiser poster, image {item.get("index", 0)+1}"><figcaption>Panel {item.get("index",0)+1}: {escape(item.get("status","unknown"))}</figcaption></figure>')
        source = post['source_url']
        cards.append(f'''<article id="post-{index}"><h2>{index}. {escape(post['society'])}</h2>
<p><a href="{escape(source, quote=True)}" target="_blank" rel="noopener noreferrer">Original post</a> · Posted {escape(post.get('published_at') or 'date unknown')} · Images: {escape(ocr.get('status','not inspected'))}</p>
<p>{escape(ocr.get('coverage_note',''))}</p><div class="evidence"><section><h3>Organiser caption</h3><pre>{escape(post.get('text') or '(No caption)')}</pre><details><summary>Machine-read image text — check against the originals</summary><pre>{escape(ocr.get('text') or '(No readable text)')}</pre></details></section><div class="pictures">{''.join(pictures) or '<p>No image was available. Review the original post.</p>'}</div></div></article>''')
    cost = receipt.get('combined_charge_usd', receipt['run'].get('usageTotalUsd'))
    page = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Grubr · Instagram pilot review</title><style>
body{{font:16px/1.5 system-ui,sans-serif;margin:0;background:#faf8f4;color:#21211f}}header,main{{max-width:1280px;margin:auto;padding:24px}}header{{border-bottom:3px solid #ef631e}}h1{{margin:0}}article{{background:white;border:1px solid #ddd;border-radius:12px;padding:24px;margin:24px 0}}h2{{margin-top:0}}.evidence{{display:grid;grid-template-columns:1fr 1.5fr;gap:24px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}}a{{color:#a53d04}}figure{{margin:0 0 18px}}img{{max-width:100%;height:auto;display:block}}figcaption{{color:#666}}details{{border-top:1px solid #ddd;padding-top:12px}}@media(max-width:760px){{.evidence{{grid-template-columns:1fr}}article{{padding:16px}}}}
</style><header><h1>Grubr · Caption & poster review</h1><p>{len(payload['posts'])} posts · Recorded actor charges: ${cost} · Pilot cap: $0.25</p><p>Read each caption and all image panels together. Confirm free food, the actual event date, UK time, venue and eligibility. A food photo or free entry does not establish free food. OCR is evidence for review, not verification. Videos have cover images only.</p><p>Approved records belong in <code>data/reviewed-events.json</code>. Rebuild with <code>python scripts/refresh.py --offline</code>. This page uses the saved images and makes no Apify requests.</p></header><main>{''.join(cards)}</main></html>'''
    (folder / 'review.html').write_text(page, encoding='utf-8')
    print(f'Review page saved: {folder / "review.html"}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=ROOT / 'work/apify-pilot')
    build(parser.parse_args().folder)
