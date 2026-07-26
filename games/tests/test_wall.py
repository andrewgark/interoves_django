from django.test import SimpleTestCase

from games.wall import get_wall_default_max_attempts


class WallDefaultMaxAttemptsTests(SimpleTestCase):
    def test_four_categories_is_5_4_3(self):
        self.assertEqual(get_wall_default_max_attempts(4), [5, 4, 3])

    def test_scales_with_n_cat(self):
        self.assertEqual(get_wall_default_max_attempts(3), [4, 3])
        self.assertEqual(get_wall_default_max_attempts(5), [6, 5, 4, 3])
