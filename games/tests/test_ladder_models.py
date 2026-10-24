from django.test import SimpleTestCase

from games.alphabetty.models import AlphabettyOffer as AlphabettyOfferPackage
from games.ladder.models import LadderOffer as LadderOfferPackage
from games.ladder.models import RaddleUiState as RaddleUiStatePackage
from games.censorly.models import CensorlyOffer as CensorlyOfferPackage
from games.models import AlphabettyOffer, CensorlyOffer, LadderOffer, RaddleUiState, WordSaladOffer
from games.salad.models import WordSaladOffer as WordSaladOfferPackage


class LadderModelLocationTests(SimpleTestCase):
    def test_reexport_keeps_the_games_tables(self):
        self.assertIs(LadderOffer, LadderOfferPackage)
        self.assertIs(RaddleUiState, RaddleUiStatePackage)
        self.assertEqual(LadderOffer._meta.app_label, 'games')
        self.assertEqual(RaddleUiState._meta.app_label, 'games')
        self.assertEqual(LadderOffer._meta.db_table, 'games_ladderoffer')
        self.assertEqual(RaddleUiState._meta.db_table, 'games_raddleuistate')

    def test_alphabetty_offer_keeps_the_games_table(self):
        self.assertIs(AlphabettyOffer, AlphabettyOfferPackage)
        self.assertEqual(AlphabettyOffer._meta.app_label, 'games')
        self.assertEqual(AlphabettyOffer._meta.db_table, 'games_alphabettyoffer')

    def test_word_salad_offer_keeps_the_games_table(self):
        self.assertIs(WordSaladOffer, WordSaladOfferPackage)
        self.assertEqual(WordSaladOffer._meta.app_label, 'games')
        self.assertEqual(WordSaladOffer._meta.db_table, 'games_wordsaladoffer')

    def test_censorly_offer_keeps_the_games_table(self):
        self.assertIs(CensorlyOffer, CensorlyOfferPackage)
        self.assertEqual(CensorlyOffer._meta.app_label, 'games')
        self.assertEqual(CensorlyOffer._meta.db_table, 'games_censorlyoffer')
        self.assertEqual(CensorlyOffer._meta.get_field('party').related_model.__name__, 'RandomCensorlyGame')
