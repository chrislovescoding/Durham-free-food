"""Extract caption events and queue separately attributed caption/poster evidence."""
from datetime import datetime, timedelta, timezone
import hashlib
import re
from zoneinfo import ZoneInfo

LONDON = ZoneInfo('Europe/London')
MONTHS = {name: index for index, names in enumerate([
    ('jan', 'january'), ('feb', 'february'), ('mar', 'march'), ('apr', 'april'),
    ('may',), ('jun', 'june'), ('jul', 'july'), ('aug', 'august'),
    ('sep', 'sept', 'september'), ('oct', 'october'), ('nov', 'november'), ('dec', 'december')
], 1) for name in names}
DATE_RE = re.compile(r'\b(\d{1,2})(?:st|nd|rd|th)?\s+(' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + r')(?:\s+(20\d{2}))?\b', re.I)
TIME_RE = re.compile(r'(?<![\d:])(\d{1,2})(?::([0-5]\d))?\s*(am|pm)\b|\b([01]?\d|2[0-3]):([0-5]\d)\b', re.I)
BOOKING_TIMING_RE = re.compile(
    r'\b(?:tickets?|bookings?|registration|sign[ -]?ups?|rsvps?|reservations?|applications?)\b'
    r'[^\n.!?]{0,100}\b(?:open(?:s|ed|ing)?|clos(?:e[sd]?|ing)|start(?:s|ing)?|end(?:s|ing)?|'
    r'go(?:es|ing)?\s+live|on\s+sale|releas(?:e[sd]?|ing)|launch(?:es|ed|ing)?|drop(?:s|ping)?|'
    r'available\s+(?:from|at|on|until))\b|'
    r'\b(?:opening|closing|release|sale|launch|start|end)\s+(?:(?:date|time)\s+)?(?:of|for)\s+'
    r'(?:tickets?|bookings?|registration|sign[ -]?ups?|rsvps?|reservations?|applications?)\b|'
    r'\b(?:register|sign[ -]?up|book|rsvp|reserve|buy tickets?)\s+(?:by|before|until)\b', re.I
)
RELATIVE_DATE_RE = re.compile(
    r'\b(?:day after tomorrow|day before yesterday|today|tomorrow|yesterday|tonight|'
    r'(?:this|next|last)\s+(?:week(?:end)?|month|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b', re.I
)
FOOD_MENTION_RE = re.compile(
    r'\b(food|pizza|snacks?|refreshments?|buffet|meals?|lunch|dinner|breakfast|curry|'
    r'pastries|cake|doughnuts?|donuts?|sandwich(?:es)?|cookies|biscuits|noodles?|soup|coffee|tea|hot chocolate)\b', re.I
)

def timestamp(value):
    if not isinstance(value, str):
        raise ValueError('Post publication time is not available')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('Timestamp needs a UTC offset')
    return parsed

def caption_datetime(text, published_at):
    """Only one event date/time; no automatic multi-event/deadline interpretation."""
    if BOOKING_TIMING_RE.search(text) or re.search(r'\b(deadline|every (?:week|monday|tuesday|wednesday|thursday|friday|saturday|sunday))\b', text, re.I):
        raise ValueError('Ticket release, registration timing or repeating dates need review')
    dates = []
    for match in re.finditer(r'\b(20\d{2})-(\d{2})-(\d{2})(?!\d)', text):
        dates.append(datetime(*map(int, match.groups())).date())
    for match in DATE_RE.finditer(text):
        day, month, year = match.groups()
        if year:
            dates.append(datetime(int(year), MONTHS[month.lower()], int(day)).date())
        else:
            posted = timestamp(published_at).astimezone(LONDON)
            plausible = []
            for candidate_year in (posted.year, posted.year + 1):
                try:
                    date = datetime(candidate_year, MONTHS[month.lower()], int(day)).date()
                except ValueError:
                    continue
                if -1 <= (date - posted.date()).days <= 90:
                    plausible.append(date)
            if len(plausible) != 1:
                raise ValueError('A yearless event date is ambiguous against the post timestamp')
            dates.append(plausible[0])
    relative_dates = {match.group(0).lower() for match in RELATIVE_DATE_RE.finditer(text)}
    if dates and relative_dates:
        # Even matching calendar dates can refer to different actions. Never
        # attach the only clock time to an event while ignoring another date.
        raise ValueError('Mixed explicit and relative date references need review')
    if relative_dates:
        if len(relative_dates) != 1 or not relative_dates <= {'today', 'tomorrow'}:
            raise ValueError('Multiple or unsupported relative event dates need review')
        dates.append(timestamp(published_at).astimezone(LONDON).date() + timedelta(days='tomorrow' in relative_dates))
    if len(set(dates)) != 1:
        raise ValueError('Caption needs one unambiguous event date')
    # A lone start time is deliberately required; ambiguous time ranges go to review.
    if re.search(r'\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*[-–]\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b', text, re.I):
        raise ValueError('Caption time range needs review')
    matches = list(TIME_RE.finditer(text))
    if len(matches) != 1:
        raise ValueError('Caption needs one unambiguous start time; multiple times need review')
    match = matches[0]
    if match[3]:
        hour = int(match[1])
        if not 1 <= hour <= 12:
            raise ValueError('Invalid 12-hour event time')
        hour = hour % 12 + (12 if match[3].lower() == 'pm' else 0)
        minute = int(match[2] or 0)
    else:
        hour, minute = int(match[4]), int(match[5])
    date = dates[0]
    starts = datetime(date.year, date.month, date.day, hour, minute, tzinfo=LONDON)
    if starts.utcoffset() != starts.replace(fold=1).utcoffset():
        raise ValueError('Ambiguous or nonexistent UK clock time needs review')
    weekday = re.search(r'\b(mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b', text, re.I)
    if weekday and weekday[1][:3].lower() != starts.strftime('%a').lower():
        raise ValueError('The weekday and event date disagree')
    return starts

def caption_entry_price(text):
    """Return only an unconditional entry price; ambiguous eligibility needs review."""
    free_entry = re.search(r'\bfree (?:entry|admission)\b', text, re.I)
    paid_prices = re.findall(r'£(\d+(?:\.\d{1,2})?)\s*(?:entry|admission|per person)\b', text, re.I)
    if free_entry:
        # Keep membership, tiers and other money in the review queue. A separate
        # sentence can qualify an apparently unconditional free-entry headline.
        if re.search(r'£\s*\d|\b\d+(?:\.\d+)?\s*(?:GBP|pounds?)\b|\b(?:members?|membership|non[ -]?members?|early[ -]?bird|tiers?)\b', text, re.I):
            raise ValueError('Conditional or tiered entry prices need review')
        for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
            if not re.search(r'\bfree (?:entry|admission)\b', sentence, re.I):
                continue
            if re.search(r'\b(?:no|not|never|only|if|with|before|after|until|when|provided|subject|limited)\b|\bfor\s+(?!(?:all|everyone|anyone)\b)', sentence, re.I):
                raise ValueError('Conditional or unconfirmed free entry needs review')
        return 0
    if len(set(paid_prices)) > 1:
        raise ValueError('Multiple entry prices need review')
    return float(paid_prices[0]) if paid_prices else None

def posts_to_events(posts, now, food_evidence):
    events, queue = [], []
    for post in posts:
        text = str(post.get('text') or '')
        food = food_evidence(text)
        image = post.get('image_ocr') if isinstance(post.get('image_ocr'), dict) else {}
        image_text = str(image.get('text') or '')
        image_food = food_evidence(image_text)
        image_review = bool(image) or post.get('origin') == 'apify' or post.get('image_review_required') is True
        incomplete_images = image_review and (
            image.get('coverage_complete') is not True or not image_text.strip()
            or image.get('status') not in ('ok', 'complete')
        )
        if not food and not image_food and not FOOD_MENTION_RE.search(text + '\n' + image_text) and not incomplete_images:
            continue
        source_id = 'social-' + post.get('platform', 'unknown')
        heading = text.strip() or image_text.strip() or 'Society post'
        candidate = {'title': heading.splitlines()[0][:150], 'source_id': source_id,
                     'source_url': post['source_url'], 'post_id': post['id'], 'society': post['society']}
        if image_review:
            # OCR is search evidence, not verification: a poster can contradict a
            # caption or describe several events. Never concatenate it into the
            # strict caption parser and silently publish the inferred fields.
            candidate['evidence'] = {
                'caption': {'text': text, 'free_food': food},
                'image_ocr': {'text': image_text, 'free_food': image_food,
                              'status': image.get('status', 'not_processed'),
                              'coverage_complete': image.get('coverage_complete') is True,
                              'expected_images': image.get('expected_images'),
                              'inspected_images': image.get('inspected_images'),
                              'images': [{key: item[key] for key in ('image_id', 'status', 'error') if key in item}
                                         for item in image.get('images', []) if isinstance(item, dict)]},
            }
            candidate['media_urls'] = post.get('media_urls', [])
            reason = ('Image text is missing, unreadable or incomplete; inspect the original images and caption'
                      if incomplete_images else 'Caption and image evidence need review together before publishing')
            queue.append({**candidate, 'kind': 'social_post_review', 'reason': reason,
                          'needs_visual_review': True})
            continue
        try:
            if not food:
                raise ValueError('Food mentioned without an explicit free-food promise')
            if post.get('origin') not in ('public_html', 'meta_api'):
                raise ValueError('Original post content needs direct verification')
            if re.search(r'\b(cancelled|canceled|postponed|last week|last night|thanks for coming|thank you for coming)\b', text, re.I):
                raise ValueError('Cancellation, postponement or retrospective caption needs review')
            starts = caption_datetime(text, post.get('published_at'))
            if starts.date() < now.astimezone(LONDON).date():
                continue
            location = re.search(r'(?:\b(?:location|venue|where)\s*[:\-]|📍)\s*([^\n]+)', text, re.I)
            if not location:
                raise ValueError('Caption has no explicitly labelled location; check the original post or poster')
            price = caption_entry_price(text)
            signup = None
            if re.search(r'\b(?:no (?:sign[ -]?up|registration|booking) (?:needed|required)|just turn up)\b', text, re.I):
                signup = False
            elif re.search(r'\b(?:sign[ -]?up|registration|booking) (?:is )?required\b', text, re.I):
                signup = True
            events.append({'id': 'social-' + hashlib.sha256(post['source_url'].encode()).hexdigest()[:16],
                'source_id': source_id, 'title': candidate['title'], 'society': post['society'],
                'starts_at': starts.isoformat(), 'ends_at': None, 'location': location[1].strip()[:250],
                'food': food, 'food_is_free': True, 'price': price, 'signup_required': signup,
                'signup_deadline': None, 'eligibility': None, 'source_url': post['source_url'],
                'source_label': post.get('platform', '').capitalize() + ' organiser post',
                'description': text, 'dietary': [], 'verified_at': post.get('observed_at'),
                'post_published_at': post.get('published_at')})
        except (ValueError, KeyError, TypeError) as error:
            queue.append({**candidate, 'kind': 'social_post_review', 'reason': str(error)})
    return events, queue
