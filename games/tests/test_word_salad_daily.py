from datetime import datetime
import json
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase
from django.urls import resolve

from games.models import CheckerType, Game, GameTaskGroup, HTMLPage, Project, Task, TaskGroup
from games.section_hub import HUB_DAILY_SECTION_IDS, SECTION_HUB_META, get_word_salad_section_hub_card
from games.section_paths import section_hub_path, section_last_path
from games.word_salad_daily import (
    WORD_SALAD_DEFAULT_PUBLISH_START,
    WORD_SALAD_PUBLISH_START_TAG,
    get_word_salad_hub_context,
    is_word_salad_number_published,
    word_salad_number_for_date,
    word_salad_publish_at,
    word_salad_publish_start,
)
from games.word_salad_sheet import parse_word_salad_csv
from games.word_salad import validate_puzzle


MOSCOW = ZoneInfo('Europe/Moscow')

SAMPLE_CSV = '''#,a,c,d,e,f,g,h,i
1,,В,А,Р,А,,КОСТРОМА,Города России
,,О,К,Т,М,,САМАРА,
,,Т,С,Р,А,,РОСТОВ,
,,П,О,М,С,,МОСКВА,
,,,,,,,ТОМСК,
,,,,,,,ПСКОВ,
,,,,,,,ОМСК,
,,,,,,,ОРСК,
6,,Т,М,Р,Н,,ВЕНЕРА,Планеты
,,Н Т Р Е,А,И,У,,НЕПТУН,
,,Р,С,Т,П,,УРАН,
,,Е,Н,Е,В,,САТУРН,
,,,,,,,МАРС,
,,,,,,,ТАТУИН,
'''


class WordSaladDailyLogicTests(SimpleTestCase):
    def _game(self, start=WORD_SALAD_DEFAULT_PUBLISH_START):
        return Game(tags={WORD_SALAD_PUBLISH_START_TAG: start})

    def test_number_for_date(self):
        game = self._game()
        self.assertIsNone(
            word_salad_number_for_date(
                game, datetime(2026, 8, 22, tzinfo=MOSCOW).date()
            )
        )
        self.assertEqual(
            word_salad_number_for_date(
                game, datetime(2026, 8, 23, tzinfo=MOSCOW).date()
            ),
            1,
        )
        self.assertEqual(
            word_salad_number_for_date(
                game, datetime(2026, 8, 24, tzinfo=MOSCOW).date()
            ),
            2,
        )

    def test_publish_at(self):
        game = self._game()
        pub1 = word_salad_publish_at(game, 1)
        pub2 = word_salad_publish_at(game, 2)
        self.assertEqual(pub1.date().isoformat(), '2026-08-23')
        self.assertEqual(pub2.date().isoformat(), '2026-08-24')

    def test_is_published(self):
        game = self._game()
        before = datetime(2026, 8, 22, 23, 0, tzinfo=MOSCOW)
        after = datetime(2026, 8, 23, 1, 0, tzinfo=MOSCOW)
        self.assertFalse(is_word_salad_number_published(game, 1, before))
        self.assertTrue(is_word_salad_number_published(game, 1, after))
        self.assertFalse(is_word_salad_number_published(game, 2, after))

    def test_hub_context_today(self):
        game = self._game()
        now = datetime(2026, 8, 23, 12, 0, tzinfo=MOSCOW)
        ctx = get_word_salad_hub_context(game, published_numbers={'1', '2'}, now=now)
        self.assertEqual(ctx['word_salad_cta_number'], '1')
        self.assertTrue(ctx['word_salad_is_today'])
        self.assertEqual(ctx['word_salad_status'], 'today')
        self.assertEqual(ctx['word_salad_section_url'], '/word_salad/')
        self.assertEqual(ctx['word_salad_play_url'], '/word_salad/last/')

    def test_hub_context_latest_when_today_missing(self):
        game = self._game()
        now = datetime(2026, 8, 24, 12, 0, tzinfo=MOSCOW)
        ctx = get_word_salad_hub_context(game, published_numbers={'1'}, now=now)
        self.assertEqual(ctx['word_salad_cta_number'], '1')
        self.assertFalse(ctx['word_salad_is_today'])
        self.assertEqual(ctx['word_salad_status'], 'latest')
        self.assertEqual(ctx['word_salad_play_url'], '/word_salad/last/')


class WordSaladSheetParseTests(SimpleTestCase):
    def test_parses_grid_words_and_theme(self):
        salads = parse_word_salad_csv(SAMPLE_CSV)
        self.assertEqual(sorted(salads), [1, 6])
        first = salads[1]
        self.assertEqual(first['theme'], 'Города России')
        self.assertEqual(len(first['grid']), 16)
        self.assertEqual(first['grid'][:4], ['В', 'А', 'Р', 'А'])
        self.assertIn('МОСКВА', first['words'])
        self.assertEqual(len(first['words']), 8)

    def test_packed_cell_uses_first_letter(self):
        salads = parse_word_salad_csv(SAMPLE_CSV)
        grid = salads[6]['grid']
        self.assertEqual(grid[4:8], ['Н', 'А', 'И', 'У'])
        validate_puzzle(grid, salads[6]['words'])


class WordSaladSectionTests(TestCase):
    def _ensure_login_modal_deps(self):
        from allauth.socialaccount.models import SocialApp
        from django.contrib.sites.models import Site

        Project.objects.get_or_create(pk='main', defaults={})
        site = Site.objects.get_current()
        for provider in ('google', 'vk'):
            app, _ = SocialApp.objects.get_or_create(
                provider=provider,
                defaults={'name': provider, 'client_id': 'test', 'secret': 'test'},
            )
            app.sites.add(site)
        for name in (
            'Правила Десяточки',
            'Правила турнирного режима',
            'Правила тренировочного режима',
        ):
            HTMLPage.objects.get_or_create(name=name, defaults={'html': ''})

    def test_game_exists_after_migration(self):
        game = Game.objects.filter(id='word_salad', project_id='sections').first()
        self.assertIsNotNone(game)
        self.assertEqual(game.name, 'Салат')
        self.assertEqual(game.outside_name, 'Салат')
        self.assertIsNotNone(word_salad_publish_start(game))
        self.assertEqual(
            word_salad_publish_start(game).date().isoformat(),
            '2026-08-23',
        )
        page = HTMLPage.objects.filter(name='section_tutorial_word_salad').first()
        self.assertIsNotNone(page)
        self.assertIn('Салат', page.html)
        self.assertIn('КОТ', page.html)
        self.assertEqual(game.section_default_rules_id, 'section_tutorial_word_salad')

    def test_hub_meta_and_daily_order(self):
        self.assertEqual(HUB_DAILY_SECTION_IDS, ('ladder', 'word_salad', 'alphabetty'))
        meta = SECTION_HUB_META['word_salad']
        self.assertEqual(meta['title'], 'Салаты')
        self.assertEqual(meta['ph_icon'], 'bowl-food')
        self.assertEqual(meta['format_credit_url'], 'https://wordsalad.online')
        self.assertEqual(meta['format_credit_name'], 'wordsalad.online')
        self.assertEqual(meta['format_credit_text'], 'салатов')
        self.assertTrue(SECTION_HUB_META['ladder'].get('wide'))
        self.assertFalse(bool(meta.get('wide')))
        self.assertEqual(section_hub_path('word_salad'), '/word_salad/')
        self.assertEqual(section_last_path('word_salad'), '/word_salad/last/')
        self.assertEqual(
            SECTION_HUB_META['ladder']['description'],
            'Разгадайте цепочку связанных слов по перемешанным подсказкам-связкам',
        )
        salad_live = resolve('/word_salad/live-state/')
        games_live = resolve('/games/word_salad/live-state/')
        self.assertEqual(salad_live.kwargs['game_id'], 'word_salad')
        self.assertEqual(games_live.kwargs['game_id'], 'word_salad')
        self.assertEqual(salad_live.func, games_live.func)

    def test_hub_card_today(self):
        game = Game.objects.get(id='word_salad')
        now = datetime(2026, 8, 23, 12, 0, tzinfo=MOSCOW)
        card = get_word_salad_section_hub_card(
            game, published_numbers={'1'}, now=now,
        )
        self.assertEqual(card['title'], 'Салаты')
        self.assertEqual(card['ph_icon'], 'bowl-food')
        self.assertEqual(card['cta_label'], 'Сегодняшний салат')
        self.assertTrue(card['is_today'])
        self.assertEqual(card['play_url'], '/word_salad/last/')

    def test_unpublished_play_404(self):
        CheckerType.objects.get_or_create(pk='word_salad')
        game = Game.objects.get(id='word_salad')
        tg = TaskGroup.objects.create(label='future', points=1)
        Task.objects.create(
            task_group=tg,
            number='1',
            task_type='word_salad',
            checker=CheckerType.objects.get(pk='word_salad'),
            checker_data='{"grid":["A","B","C","D","H","G","F","E","I","J","K","L","P","O","N","M"],"words":["ABCDEFGHIJKLMNOP"]}',
            points=1,
        )
        GameTaskGroup.objects.create(game=game, task_group=tg, number='999', name='Салат #999')
        resp = self.client.get('/word_salad/999/')
        self.assertEqual(resp.status_code, 404)

    def test_play_shows_format_credit(self):
        CheckerType.objects.get_or_create(pk='word_salad')
        game = Game.objects.get(id='word_salad')
        tg = TaskGroup.objects.create(label='salad-1', points=1)
        Task.objects.create(
            task_group=tg,
            number='1',
            task_type='word_salad',
            checker=CheckerType.objects.get(pk='word_salad'),
            checker_data='{"grid":["A","B","C","D","H","G","F","E","I","J","K","L","P","O","N","M"],"words":["ABCDEFGHIJKLMNOP"]}',
            points=1,
        )
        GameTaskGroup.objects.create(game=game, task_group=tg, number='1', name='Салат #1')
        self._ensure_login_modal_deps()
        resp = self.client.get('/word_salad/1/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('https://wordsalad.online', html)
        self.assertIn('wordsalad.online', html)
        self.assertIn('мы благодарны им за идею салатов', html)

    def test_archive_cards_show_theme_and_word_count(self):
        from django.template.loader import render_to_string
        from games.views.new_ui import _ladder_task_group_rows

        CheckerType.objects.get_or_create(pk='word_salad')
        game = Game.objects.get(id='word_salad')
        tg = TaskGroup.objects.create(label='salad-theme', points=1)
        Task.objects.create(
            task_group=tg,
            number='1',
            task_type='word_salad',
            checker=CheckerType.objects.get(pk='word_salad'),
            checker_data=json.dumps({
                'grid': ['A', 'B', 'C', 'D', 'H', 'G', 'F', 'E', 'I', 'J', 'K', 'L', 'P', 'O', 'N', 'M'],
                'words': ['КОСТРОМА', 'САМАРА', 'РОСТОВ', 'МОСКВА', 'ТОМСК', 'ПСКОВ'],
            }, ensure_ascii=False),
            text='Города России',
            points=1,
        )
        link = GameTaskGroup.objects.create(
            game=game, task_group=tg, number='1', name='Салат #1',
        )
        link.n_tasks = 1
        rows = _ladder_task_group_rows(
            [link], game, item_label='Салат',
        )
        self.assertEqual(rows[0]['salad_meta'], 'Города России · 6 слов')
        html = render_to_string(
            'new/_task_group_rows.html',
            {'task_group_rows': rows, 'game': game},
        )
        self.assertIn('Города России · 6 слов', html)

        self._ensure_login_modal_deps()
        resp = self.client.get('/word_salad/')
        self.assertEqual(resp.status_code, 200)
        page = resp.content.decode('utf-8')
        self.assertIn('Города России · 6 слов', page)
        self.assertIn(
            'Сетка 4×4: найдите все слова, проводя дорожки по соседним буквам.',
            page,
        )

    def test_hub_includes_salad_card(self):
        self._ensure_login_modal_deps()
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode('utf-8')
        self.assertIn('Салаты', html)
        self.assertIn('ph-bowl-food', html)
        self.assertIn('/word_salad/', html)
        self.assertLess(html.find('Салат'), html.find('Алфавитка'))
        self.assertIn('new-hub-section--wide', html)
        self.assertIn(
            'Разгадайте цепочку связанных слов по перемешанным подсказкам-связкам',
            html,
        )
