from django.test import SimpleTestCase
from django.urls import resolve, reverse


class MicrositesUrlTests(SimpleTestCase):
    def test_nutrimatic_home_resolves(self):
        m = resolve("/nutrimatic-ru/")
        self.assertEqual(m.url_name, "nutrimatic_home")

    def test_nutrimatic_static_path_resolves(self):
        m = resolve("/nutrimatic-ru/robots.txt")
        self.assertEqual(m.kwargs["rel_path"], "robots.txt")

    def test_eurovision_booklet_2026_resolves(self):
        m = resolve("/eurovision_booklet/2026/")
        self.assertEqual(m.url_name, "eurovision_booklet_2026")

    def test_eurovision_booklet_2026_reverse(self):
        self.assertEqual(
            reverse("eurovision_booklet_2026"),
            "/eurovision_booklet/2026/",
        )
