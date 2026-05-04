from django.test import Client, SimpleTestCase
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

    def test_eurovision_booklet_pdf_reverse(self):
        self.assertEqual(
            reverse(
                "eurovision_booklet_pdf",
                kwargs={"filename": "eurovision2026_sf1_ru.pdf"},
            ),
            "/eurovision_booklet/2026/pdf/eurovision2026_sf1_ru.pdf",
        )

    def test_eurovision_booklet_200_and_pdf_404(self):
        c = Client()
        r = c.get("/eurovision_booklet/2026/")
        self.assertEqual(r.status_code, 200)
        bad = c.get("/eurovision_booklet/2026/pdf/not-a-booklet.pdf")
        self.assertEqual(bad.status_code, 404)
