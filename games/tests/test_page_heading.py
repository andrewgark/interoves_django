"""Page heading: rules ? sits next to the title, not the tagline."""

import re
from pathlib import Path
from types import SimpleNamespace

from django.template.loader import render_to_string
from django.test import SimpleTestCase

TEMPLATES_NEW = Path(__file__).resolve().parents[2] / 'static' / 'templates' / 'new'


def section_header_inner(html: str) -> str:
    match = re.search(
        r'<div class="new-section-header(?:\s[^"]*)?">(.*?)</div>',
        html,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError('new-section-header not found')
    return match.group(1)


def assert_rules_beside_title(test_case, html):
    inner = section_header_inner(html)
    test_case.assertNotRegex(
        inner,
        r'<div>\s*<h1',
        msg='title and tagline must not share a wrapper that widens the ? row',
    )
    h1_at = inner.find('<h1')
    rules_at = inner.find('new-rules-trigger')
    theme_at = inner.find('new-theme-line')
    test_case.assertNotEqual(h1_at, -1)
    test_case.assertNotEqual(rules_at, -1)
    test_case.assertLess(h1_at, rules_at)
    if theme_at != -1:
        test_case.assertLess(
            rules_at,
            theme_at,
            'rules button must come before the tagline so ? sits next to the title',
        )


class PageHeadingPartialTests(SimpleTestCase):
    def test_long_tagline_does_not_wrap_title_and_rules(self):
        html = render_to_string(
            'new/partials/page_heading.html',
            {
                'heading': 'Салат №1',
                'tagline': 'Сетка 4×4: найдите все слова по соседним буквам.',
                'show_rules': True,
                'is_daily_single_task': True,
                'game': SimpleNamespace(theme='', tags={}),
            },
        )
        assert_rules_beside_title(self, html)
        self.assertIn('Салат №1', html)
        self.assertIn('Сетка 4×4', html)
        self.assertNotIn('new-eye-toggle', html)

    def test_hub_shows_eye_and_rules_before_tagline(self):
        html = render_to_string(
            'new/partials/page_heading.html',
            {
                'heading': 'Лесенка',
                'tagline': 'Одна лестница слов в день',
                'show_rules': True,
                'is_daily_single_task': False,
                'game': SimpleNamespace(theme='', tags={}),
            },
        )
        assert_rules_beside_title(self, html)
        self.assertIn('new-eye-toggle', html)

    def test_play_and_hub_templates_share_partial(self):
        for rel in (
            'task_group.html',
            'alphabetty_play.html',
            'alphabetty_hub.html',
            'game_page.html',
        ):
            text = (TEMPLATES_NEW / rel).read_text(encoding='utf-8')
            self.assertIn(
                'new/partials/page_heading.html',
                text,
                msg='{} should use the shared page heading'.format(rel),
            )
