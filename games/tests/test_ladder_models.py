from django.test import SimpleTestCase

from games.ladder.models import LadderOffer as LadderOfferPackage
from games.ladder.models import RaddleUiState as RaddleUiStatePackage
from games.models import LadderOffer, RaddleUiState


class LadderModelLocationTests(SimpleTestCase):
    def test_reexport_keeps_the_games_tables(self):
        self.assertIs(LadderOffer, LadderOfferPackage)
        self.assertIs(RaddleUiState, RaddleUiStatePackage)
        self.assertEqual(LadderOffer._meta.app_label, 'games')
        self.assertEqual(RaddleUiState._meta.app_label, 'games')
        self.assertEqual(LadderOffer._meta.db_table, 'games_ladderoffer')
        self.assertEqual(RaddleUiState._meta.db_table, 'games_raddleuistate')
