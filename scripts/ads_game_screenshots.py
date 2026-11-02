"""Ad creatives for the Direct game groups: each game shown mid-play.

For every daily game it captures a 3× close-up of a puzzle being solved and
composes three images in artifacts/ads/images/:

    <game>-a-1x1.jpg    the close-up alone (1080×1080)
    <game>-b-1x1.jpg    a green band with the game's name and mechanic on top
    <game>-b-16x9.jpg   the band on the left, the close-up on the right (1080×607)

Puzzles are archive or evergreen ones, so an ad never spoils a puzzle in the
free window and never goes stale:

- Лесенка — the tutorial ladder (ПЕРВАЯ → … → ЛАСТОЧКА), two words solved;
- Салатик №22 «Европейские столицы», ПАРИЖ and ВЕНА found, «БЕРЛ…» in progress;
- Алфавитка №30 with six guesses;
- Цензурка — production puzzle --censorly-number (an old free one about to
  leave the free window), a few common words guessed; the title stays hidden.

Ladder, Salad and Alphabet are played on a local server (--local, a dev
server on a copy of db.sqlite3 with these archive puzzles), Censorly on
production; that is one anonymous Censorly game on production.

Usage:
  ../venv/interoves_django/bin/python scripts/ads_game_screenshots.py \\
      --local http://127.0.0.1:8790 --censorly-number 14
"""

import argparse
import base64
import io
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'artifacts' / 'ads' / 'images'
PROD = 'https://interoves.com'
SCALE = 3
ACCENT = '#1f6f5e'
PAGE_BG = '#f5f7f6'

HIDE_CSS = '''
.new-nav, [role=dialog], .new-rules-modal, .new-site-version-hint, .new-signup-prompt,
.new-onboarding-followup, [data-stuck-nudge], .new-rules-banner, .new-back-link,
[class*=tutorial-shell], #djDebug { display: none !important; }
'''

CARDS = {
    'censorly': ('Цензурка', 'Угадайте статью Википедии, в которой скрыты все слова'),
    'ladder': ('Лесенка', 'Каждое слово цепочки связано с соседним: ПЯТАЯ → ЗАПЯТАЯ'),
    'salad': ('Салатик', 'Найдите все слова на тему, проводя дорожки по буквам'),
    'alphabetty': ('Алфавитка', 'Угадайте слово: каждая догадка сужает диапазон по алфавиту'),
}


def _open(browser, url):
    context = browser.new_context(
        viewport={'width': 420, 'height': 1100}, device_scale_factor=SCALE, locale='ru-RU',
    )
    page = context.new_page()
    page.goto(url, wait_until='networkidle', timeout=60000)
    page.add_style_tag(content=HIDE_CSS)
    page.evaluate("document.body.classList.remove('new-modal-open')")
    page.wait_for_timeout(800)
    return context, page


def _union(page, selectors):
    """Bounding region in page coordinates (moves can scroll the page)."""
    scroll_y = page.evaluate('window.scrollY')
    boxes = [page.locator(s).first.bounding_box() for s in selectors]
    boxes = [dict(b, y=b['y'] + scroll_y) for b in boxes if b]
    top = min(b['y'] for b in boxes)
    bottom = max(b['y'] + b['height'] for b in boxes)
    left = min(b['x'] for b in boxes)
    right = max(b['x'] + b['width'] for b in boxes)
    return left, top, right, bottom


def _square(page, region, pad=16):
    left, top, right, bottom = region
    side = max(right - left, bottom - top) + 2 * pad
    cx, cy = (left + right) / 2, (top + bottom) / 2
    clip = {'x': max(0, cx - side / 2), 'y': max(0, cy - side / 2), 'width': side, 'height': side}
    return Image.open(io.BytesIO(page.screenshot(clip=clip, full_page=True))).convert('RGB')


def capture_censorly(browser, number):
    context, page = _open(browser, f'{PROD}/censorly/{number}/')
    for word in ('год', 'город', 'является', 'первый', 'время', 'большой', 'страна'):
        page.fill('#censorly-word', word)
        page.click('#censorly-submit')
        page.wait_for_timeout(700)
    # Guessing scrolls an inner container; a reload keeps the progress
    # (stored on the server) and starts from the top again.
    page.reload(wait_until='networkidle')
    page.add_style_tag(content=HIDE_CSS)
    page.evaluate("document.body.classList.remove('new-modal-open')")
    page.wait_for_timeout(800)
    left, top, _right, _bottom = _union(page, ['#censorly-body'])
    shot = _square(page, (left, top, left + 360, top + 360), pad=10)
    context.close()
    return shot


def capture_ladder(browser, base):
    context, page = _open(browser, f'{base}/ladder/tutorial/')
    for prefix, word in (('Слово 2', 'ПЯТАЯ'), ('Слово 3', 'ЗАПЯТАЯ')):
        field = page.locator(f'input[aria-label^="{prefix}"]:visible').first
        field.click(force=True)
        field.type(word, delay=40)
        page.wait_for_timeout(2500)
    left, top, right, bottom = _union(page, ['.new-raddle-table'])
    shot = _square(page, (left, top - 48, right, bottom))
    context.close()
    return shot


def capture_salad(browser, base):
    context, page = _open(browser, f'{base}/salad/22/')

    def tap(indexes):
        for index in indexes:
            page.locator(f'[data-word-salad-cell][data-index="{index}"]').first.dispatch_event(
                'pointerdown', {'isPrimary': True, 'button': 0, 'pointerId': 1},
            )
            page.wait_for_timeout(150)
        page.wait_for_timeout(1500)

    tap([0, 4, 5, 10, 15])  # ПАРИЖ
    tap([3, 2, 7, 11])  # ВЕНА
    tap([1, 2, 5, 6])  # БЕРЛ… left in progress
    left, top, right, bottom = _union(page, ['.new-word-salad__grid'])
    shot = _square(page, (left, top - 55, right, bottom + 30))
    context.close()
    return shot


def capture_alphabetty(browser, base):
    context, page = _open(browser, f'{base}/alphabetty/30/')
    for word in ('мир', 'кот', 'собака', 'лампа', 'пирог', 'облако'):
        page.fill('#ab-word', word)
        page.click('#ab-submit')
        page.wait_for_timeout(900)
    region = _union(page, ['#ab-after-block', '#ab-input-zone', '#ab-before-block'])
    shot = _square(page, region)
    context.close()
    return shot


def _data_url(image):
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


FONT = "'Clear Sans','Helvetica Neue',Arial,sans-serif"
SQUARE_B = f'''<body style="margin:0"><div style="width:1080px;height:1080px;display:flex;flex-direction:column;
background:{PAGE_BG};font-family:{FONT}"><div style="background:{ACCENT};color:#fff;padding:52px 64px 48px">
<div style="font-size:84px;font-weight:700;line-height:1">{{title}}</div>
<div style="font-size:42px;line-height:1.25;margin-top:20px">{{hook}}</div></div>
<div style="flex:1;background:url({{img}}) center/contain no-repeat {PAGE_BG}"></div></div></body>'''
WIDE_B = f'''<body style="margin:0"><div style="width:1080px;height:607px;display:flex;
background:{PAGE_BG};font-family:{FONT}"><div style="width:473px;background:{ACCENT};color:#fff;
padding:0 44px;display:flex;flex-direction:column;justify-content:center;box-sizing:border-box">
<div style="font-size:60px;font-weight:700;line-height:1">{{title}}</div>
<div style="font-size:36px;line-height:1.28;margin-top:22px">{{hook}}</div></div>
<div style="width:607px;background:url({{img}}) center/contain no-repeat {PAGE_BG}"></div></div></body>'''


def compose(browser, game, shot):
    title, hook = CARDS[game]
    shot.resize((1080, 1080), Image.LANCZOS).save(OUT / f'{game}-a-1x1.jpg', quality=90, optimize=True)
    page = browser.new_page(viewport={'width': 1080, 'height': 1080})
    for name, template, size in (('b-1x1', SQUARE_B, (1080, 1080)), ('b-16x9', WIDE_B, (1080, 607))):
        page.set_viewport_size({'width': size[0], 'height': size[1]})
        page.set_content(template.format(title=title, hook=hook, img=_data_url(shot)))
        page.wait_for_timeout(200)
        image = Image.open(io.BytesIO(page.screenshot())).convert('RGB')
        image.save(OUT / f'{game}-{name}.jpg', quality=90, optimize=True)
    page.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--local', default='http://127.0.0.1:8790')
    parser.add_argument('--censorly-number', type=int, required=True)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        shots = {
            'ladder': capture_ladder(browser, args.local),
            'salad': capture_salad(browser, args.local),
            'alphabetty': capture_alphabetty(browser, args.local),
            'censorly': capture_censorly(browser, args.censorly_number),
        }
        for game, shot in shots.items():
            compose(browser, game, shot)
            print(game, 'ok')
        browser.close()


if __name__ == '__main__':
    main()
