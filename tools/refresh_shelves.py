"""Regenerate the Bookshelf sections from the Goodreads RSS feeds.

    python tools/refresh_shelves.py

Why RSS and not the widgets:

  custom_widget  carries author and rating, but its shelf, book count and title
                 are server-side settings keyed to the user id, so an account
                 gets exactly one of them. Two shelves need two, which Goodreads
                 will not give. It also writes into a hard-coded element id, so
                 two of them on one page overwrite each other.
  grid_widget    honours ?shelf= and takes the element id as a parameter, but
                 carries only a cover and a title. No author, no rating.
  list_rss       carries everything and honours ?shelf=, but sends no CORS
                 headers, so a browser cannot fetch it.

So the feed gets read here, on a machine rather than in a visitor's browser,
and the result is committed as plain HTML. The page needs no JavaScript to
show the shelf, and there is nothing to fail while somebody is looking at it.
.github/workflows/refresh-shelves.yml runs this daily.

Ratings come from `user_rating` in the feed, which is the rating the account
holder gave, not the community average. Goodreads now accepts partial stars on
the site, but the feed still rounds them to whole stars, and the shelf pages
that show the exact value need a sign-in. So exact scores live in
tools/ratings.json, keyed by the Goodreads book id (the number in the book's
URL, also `book_id` in the feed):

    {"17308035": 4.5, "40597810": 3.75}

An entry there wins over the feed. Books without one show the feed's rating.
Scores are clamped to 0 to 5, and any fraction is drawn as a partial star.
"""

import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone

USER = '195366376'
FEED = 'https://www.goodreads.com/review/list_rss/{user}?shelf={shelf}&page={page}'
PROFILE = 'https://www.goodreads.com/review/list/{user}?shelf={shelf}'
UA = 'Mozilla/5.0 (compatible; shelf-refresh/1.0)'
MAX_PAGES = 10

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE = os.path.join(HERE, os.pardir, 'bookshelf.html')
RATINGS = os.path.join(HERE, 'ratings.json')

# Star geometry, matching .book .stars in style.css: each star is 12.6px wide
# on a 16px pitch, five of them in 77px.
STAR_W, STAR_PITCH, STARS_W = 12.6, 16, 77

SHELVES = [
    {'slug': 'currently-reading', 'marker': 'reading', 'heading': 'Currently reading', 'stars': False},
    {'slug': 'read', 'marker': 'read', 'heading': 'Read', 'stars': True},
]


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode('utf-8', 'replace')


def field(item, name):
    m = re.search(r'<%s>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</%s>' % (name, name), item, re.S)
    return html.unescape(m.group(1)).strip() if m else ''


def exact_ratings():
    if not os.path.exists(RATINGS):
        return {}
    with open(RATINGS, encoding='utf-8') as f:
        return {str(k): float(v) for k, v in json.load(f).items()}


def books(shelf, exact):
    out = []
    for page in range(1, MAX_PAGES + 1):
        xml = fetch(FEED.format(user=USER, shelf=shelf, page=page))
        items = re.findall(r'<item>(.*?)</item>', xml, re.S)
        if not items:
            break
        for it in items:
            # Covers show at 54px wide. Goodreads encodes the size in the file
            # name, so ask for 160px tall: about 2x for the display size, and
            # roughly 5KB each against 25KB for the large variant.
            cover = (field(it, 'book_medium_image_url')
                     or field(it, 'book_image_url')
                     or field(it, 'book_large_image_url'))
            cover = re.sub(r'\._S[XY]\d+_(?=\.[a-z]+$)', '._SY160_', cover)
            title = re.sub(r'\s+By:.*$', '', field(it, 'title')).strip()
            try:
                rating = float(field(it, 'user_rating') or 0)
            except ValueError:
                rating = 0.0
            rating = exact.get(field(it, 'book_id'), rating)
            out.append({
                'title': title,
                'author': field(it, 'author_name'),
                'cover': cover,
                'link': field(it, 'link'),
                'rating': round(max(0.0, min(5.0, rating)), 2),
            })
        if len(items) < 100:
            break
    return out


def esc(s):
    return html.escape(s, quote=True)


def stars(rating):
    # The fill runs over the whole stars and then across part of the next one,
    # so 3.5 covers three stars and half of the fourth, not 70% of the strip.
    whole = int(rating)
    fill = whole * STAR_PITCH + (rating - whole) * STAR_W
    return ('<span class="stars" role="img" aria-label="Rated %g out of 5" style="--fill:%.1f%%"></span>'
            % (rating, min(100.0, fill / STARS_W * 100)))


def render(shelf, entries):
    stars_col = shelf['stars']
    fallback = PROFILE.format(user=USER, shelf=shelf['slug'])
    rows = []
    for b in entries:
        bits = ['      <a class="book" href="%s" target="_blank" rel="noopener">' % esc(b['link'] or fallback)]
        if b['cover']:
            bits.append('        <img src="%s" alt="" loading="lazy">' % esc(b['cover']))
        bits.append('        <span><span class="title">%s</span><span class="author">%s</span></span>'
                    % (esc(b['title']), esc(b['author'])))
        if stars_col and b['rating']:
            bits.append('        ' + stars(b['rating']))
        bits.append('      </a>')
        rows.append('\n'.join(bits))

    if not rows:
        return ''

    return (
        '    <div class="section"><h2>%s</h2><span class="count">%02d titles</span></div>\n'
        '    <div class="list">\n%s\n    </div>\n'
        % (shelf['heading'], len(entries), '\n'.join(rows))
    )


def main():
    page = open(PAGE, encoding='utf-8').read()
    total = {}
    exact = exact_ratings()

    for shelf in SHELVES:
        entries = books(shelf['slug'], exact)
        total[shelf['slug']] = len(entries)
        block = render(shelf, entries)
        start = '<!-- shelf:%s:start -->' % shelf['marker']
        end = '<!-- shelf:%s:end -->' % shelf['marker']
        pattern = re.compile(re.escape(start) + '.*?' + re.escape(end), re.S)
        if not pattern.search(page):
            sys.exit('markers %s / %s not found in bookshelf.html' % (start, end))
        page = pattern.sub(start + '\n' + block + '    ' + end, page)

    stamp = datetime.now(timezone.utc).strftime('%d %B %Y')
    page = re.sub(
        r'(<p class="status" id="shelf-status">).*?(</p>)',
        lambda m: m.group(1) + 'Synced from Goodreads, ' + stamp + m.group(2),
        page,
    )

    open(PAGE, 'w', encoding='utf-8', newline='').write(page)
    print('currently reading: %d' % total['currently-reading'])
    print('read:              %d' % total['read'])
    print('written to bookshelf.html')


if __name__ == '__main__':
    main()
