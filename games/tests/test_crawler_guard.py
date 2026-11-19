import asyncio

from django.test import SimpleTestCase

from games.realtime.crawler_guard import CrawlerGuardASGIMiddleware

META = b'Mozilla/5.0 Chrome/145.0.0.0 Safari/537.36 (compatible; meta-externalagent/1.1)'


def _run(path, query=b'', ua=META):
    events, reached = [], []

    async def app(scope, receive, send):
        reached.append(scope['path'])
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})

    async def receive():
        return {'type': 'http.request', 'body': b'', 'more_body': False}

    async def send(message):
        events.append(message)

    scope = {'type': 'http', 'method': 'GET', 'path': path, 'query_string': query,
             'headers': [(b'user-agent', ua)]}
    asyncio.run(CrawlerGuardASGIMiddleware(app)(scope, receive, send))
    return events[0]['status'], reached


class CrawlerGuardTests(SimpleTestCase):
    def test_ai_crawler_is_refused_on_login_links_and_table_variants(self):
        self.assertEqual(_run('/accounts/vk/login/', b'next=/x/')[0], 403)
        self.assertEqual(_run('/censorly/results/', b'month=2026-11&snapshot=abc')[0], 403)
        self.assertEqual(_run('/ladder/results/', b'sort=time', ua=b'x Reflectionbot/1.0')[0], 403)

    def test_ordinary_pages_and_people_pass(self):
        self.assertEqual(_run('/censorly/results/', b'month=2026-11'), (200, ['/censorly/results/']))
        self.assertEqual(_run('/ladder/')[0], 200)
        self.assertEqual(_run('/accounts/vk/login/', b'next=/x/', ua=b'Mozilla/5.0 Chrome/157')[0], 200)
