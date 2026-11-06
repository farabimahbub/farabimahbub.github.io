"""Regenerate the Bookshelf sections from a Goodreads library export.

    python tools/refresh_shelves.py [path/to/goodreads_library_export.csv]

Without a path it takes the newest goodreads_library_export*.csv in the folder
that holds the repository. Export from Goodreads under My Books > Import and
export > Export library, then run this, commit bookshelf.html and push.

Why an export and not a feed: Goodreads stopped serving its shelf and updates
RSS feeds in October 2026 and its robots.txt now disallows both, and the shelf
pages need a sign-in. The export is the one supported way out, and it carries
the exact rating, quarter stars included, which the feeds never did.

The export also holds private notes and reviews, so it stays out of the
repository (.gitignore covers it). Only title, author, rating, shelf and dates
are read from it.

Covers are not in the export. tools/covers.json maps Goodreads book id to a
cover URL. A book missing there gets an Open Library cover looked up by ISBN,
and the result is saved to covers.json. A book with no ISBN, or none that Open
Library knows, shows no cover until a URL is added to covers.json by hand.
"""

import csv
import glob
import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime

UA = 'Mozilla/5.0 (compatible; bookshelf-builder/1.0)'
BOOK = 'https://www.goodreads.com/book/show/{id}'
OPEN_LIBRARY = 'https://covers.openlibrary.org/b/isbn/{isbn}-M.jpg'

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.join(HERE, os.pardir)
PAGE = os.path.join(REPO, 'bookshelf.html')
COVERS = os.path.join(HERE, 'covers.json')

# Star geometry, matching .book .stars in style.css: each star is 12.6px wide
# on a 16px pitch, five of them in 77px.
STAR_W, STAR_PITCH, STARS_W = 12.6, 16, 77

SHELVES = [
    {'slug': 'currently-reading', 'marker': 'reading', 'heading': 'Currently reading', 'stars': False},
    {'slug': 'read', 'marker': 'read', 'heading': 'Read', 'stars': True},
]


def export_path():
    if len(sys.argv) > 1:
        return sys.argv[1]
    found = glob.glob(os.path.join(REPO, os.pardir, 'goodreads_library_export*.csv'))
    if not found:
        sys.exit('no goodreads_library_export*.csv next to the repository; pass a path')
    return max(found, key=os.path.getmtime)


def isbn(row):
    # The export wraps ISBNs as ="9780099584353" so spreadsheets keep them as text.
    for col in ('ISBN13', 'ISBN'):
        digits = re.sub(r'[^0-9X]', '', row.get(col, ''))
        if digits:
            return digits
    return ''


def date(row, col):
    # Goodreads writes 2026/06/01. Blank sorts last.
    return row.get(col, '').strip() or '0000/00/00'


def read_export(path):
    shelves = {s['slug']: [] for s in SHELVES}
    with open(path, encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            shelf = row.get('Exclusive Shelf', '')
            if shelf not in shelves:
                continue
            try:
                rating = float(row.get('My Rating') or 0)
            except ValueError:
                rating = 0.0
            shelves[shelf].append({
                'id': row['Book Id'].strip(),
                'title': row['Title'].strip(),
                'author': row['Author'].strip(),
                'isbn': isbn(row),
                'rating': round(max(0.0, min(5.0, rating)), 2),
                # Finished books by the day they were finished, falling back to
                # the day they were shelved. Newest first.
                'when': date(row, 'Date Read') if row.get('Date Read', '').strip() else date(row, 'Date Added'),
            })
    for entries in shelves.values():
        entries.sort(key=lambda b: b['when'], reverse=True)
    return shelves


def open_library_cover(number):
    # ?default=false makes a missing cover a 404 instead of a blank image.
    url = OPEN_LIBRARY.format(isbn=number)
    req = urllib.request.Request(url + '?default=false', method='HEAD', headers={'User-Agent': UA})
    try:
        with urllib.request.urlopen(req, timeout=30):
            return url
    except urllib.error.HTTPError:
        return ''


def covers_for(shelves):
    covers = {}
    if os.path.exists(COVERS):
        with open(COVERS, encoding='utf-8') as f:
            covers = json.load(f)
    missing = []
    for entries in shelves.values():
        for b in entries:
            if b['id'] not in covers and b['isbn']:
                found = open_library_cover(b['isbn'])
                if found:
                    covers[b['id']] = found
            if b['id'] not in covers:
                missing.append(b['title'])
            b['cover'] = covers.get(b['id'], '')
    with open(COVERS, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(covers, f, indent=2, sort_keys=True, ensure_ascii=False)
        f.write('\n')
    return missing


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
    rows = []
    for b in entries:
        bits = ['      <a class="book" href="%s" target="_blank" rel="noopener">' % esc(BOOK.format(id=b['id']))]
        if b['cover']:
            bits.append('        <img src="%s" alt="" loading="lazy">' % esc(b['cover']))
        else:
            # Holds the cover column so the title stays in its place.
            bits.append('        <span aria-hidden="true"></span>')
        bits.append('        <span><span class="title">%s</span><span class="author">%s</span></span>'
                    % (esc(b['title']), esc(b['author'])))
        if shelf['stars'] and b['rating']:
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
    source = os.path.abspath(export_path())
    shelves = read_export(source)
    missing = covers_for(shelves)
    page = open(PAGE, encoding='utf-8').read()

    for shelf in SHELVES:
        block = render(shelf, shelves[shelf['slug']])
        start = '<!-- shelf:%s:start -->' % shelf['marker']
        end = '<!-- shelf:%s:end -->' % shelf['marker']
        pattern = re.compile(re.escape(start) + '.*?' + re.escape(end), re.S)
        if not pattern.search(page):
            sys.exit('markers %s / %s not found in bookshelf.html' % (start, end))
        page = pattern.sub(lambda m: start + '\n' + block + '    ' + end, page)

    # Dated by the export, which is when the shelf was last true.
    stamp = datetime.fromtimestamp(os.path.getmtime(source)).strftime('%d %B %Y').lstrip('0')
    page = re.sub(
        r'(<p class="status" id="shelf-status">).*?(</p>)',
        lambda m: m.group(1) + 'Updated from Goodreads, ' + stamp + m.group(2),
        page,
    )

    open(PAGE, 'w', encoding='utf-8', newline='').write(page)
    print('export:            %s' % source)
    print('currently reading: %d' % len(shelves['currently-reading']))
    print('read:              %d' % len(shelves['read']))
    if missing:
        print('no cover for:      %s' % '; '.join(missing))
    print('written to bookshelf.html and tools/covers.json')


if __name__ == '__main__':
    main()
