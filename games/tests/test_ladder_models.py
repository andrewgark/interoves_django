from django.test import SimpleTestCase

from games.models import AlphabettyOffer, CensorlyOffer, LadderOffer, RaddleUiState, WordSaladOffer
from games.puzzles.alphabetty.models import AlphabettyOffer as AlphabettyOfferImpl
from games.puzzles.censorly.models import CensorlyOffer as CensorlyOfferImpl
from games.puzzles.raddle.models import LadderOffer as LadderOfferImpl
from games.puzzles.raddle.models import RaddleUiState as RaddleUiStateImpl
from games.puzzles.salad.models import WordSaladOffer as WordSaladOfferImpl


class LadderModelLocationTests(SimpleTestCase):
    def test_reexport_keeps_the_games_tables(self):
        self.assertIs(LadderOffer, LadderOfferImpl)
        self.assertIs(RaddleUiState, RaddleUiStateImpl)
        self.assertEqual(LadderOffer._meta.app_label, 'games')
        self.assertEqual(RaddleUiState._meta.app_label, 'games')
        self.assertEqual(LadderOffer._meta.db_table, 'games_ladderoffer')
        self.assertEqual(RaddleUiState._meta.db_table, 'games_raddleuistate')

    def test_alphabetty_offer_keeps_the_games_table(self):
        self.assertIs(AlphabettyOffer, AlphabettyOfferImpl)
        self.assertEqual(AlphabettyOffer._meta.app_label, 'games')
        self.assertEqual(AlphabettyOffer._meta.db_table, 'games_alphabettyoffer')

    def test_word_salad_offer_keeps_the_games_table(self):
        self.assertIs(WordSaladOffer, WordSaladOfferImpl)
        self.assertEqual(WordSaladOffer._meta.app_label, 'games')
        self.assertEqual(WordSaladOffer._meta.db_table, 'games_wordsaladoffer')

    def test_censorly_offer_keeps_the_games_table(self):
        self.assertIs(CensorlyOffer, CensorlyOfferImpl)
        self.assertEqual(CensorlyOffer._meta.app_label, 'games')
        self.assertEqual(CensorlyOffer._meta.db_table, 'games_censorlyoffer')
        self.assertEqual(CensorlyOffer._meta.get_field('party').related_model.__name__, 'RandomCensorlyGame')
