"""Square 1080×1080 game screenshots for Direct text-and-image ads.

Opens fixed puzzles on production as an anonymous visitor (mobile layout,
540 px at 2× scale), hides navigation, dialogs and hints, and saves the top
of the game card to artifacts/ads/images/<game>-1080.jpg. The Alphabetty
shot needs a few guesses on the board, so it submits six words: that is one
anonymous game start on production.

Usage: ../venv/interoves_django/bin/python scripts/ads_game_screenshots.py
"""

from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'artifacts' / 'ads' / 'images'
SITE = 'https://interoves.com'
GAMES = {
    'ladder': '/ladder/118/',
    'salad': '/salad/72/',
    'alphabetty': '/alphabetty/94/',
    'censorly': '/censorly/20/',
}
ALPHABETTY_GUESSES = ('мир', 'кот', 'собака', 'лампа', 'пирог', 'облако')
# Alphabetty's guesses start below an empty area at the top of the card.
EXTRA_TOP = {'alphabetty': 105}
HIDE_CSS = '''
.new-nav, [role=dialog], .new-rules-modal, .new-site-version-hint, .new-signup-prompt,
.new-onboarding-followup, [data-stuck-nudge], .new-rules-banner,
.new-daily-game-header__back, .new-back-link { display: none !important; }
'''
SIDE = 540  # CSS px; ×2 device scale = 1080 px


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        for name, path in GAMES.items():
            context = browser.new_context(
                viewport={'width': SIDE, 'height': 1200}, device_scale_factor=2, locale='ru-RU',
            )
            page = context.new_page()
            page.goto(SITE + path, wait_until='networkidle', timeout=60000)
            page.add_style_tag(content=HIDE_CSS)
            page.evaluate("document.body.classList.remove('new-modal-open')")
            if name == 'alphabetty':
                for word in ALPHABETTY_GUESSES:
                    page.fill('#ab-word', word)
                    page.click('#ab-submit')
                    page.wait_for_timeout(900)
            page.wait_for_timeout(800)
            box = page.locator('.new-taskcard').first.bounding_box()
            png = OUT / f'{name}-1080.png'
            page.screenshot(path=str(png), clip={
                'x': 0, 'y': max(0, box['y'] - 12 + EXTRA_TOP.get(name, 0)), 'width': SIDE, 'height': SIDE,
            })
            context.close()
            jpg = OUT / f'{name}-1080.jpg'
            Image.open(png).convert('RGB').save(jpg, quality=88, optimize=True)
            png.unlink()
            print(jpg.relative_to(ROOT))
        browser.close()


if __name__ == '__main__':
    main()
