"""Download the DB-IP "IP to Country Lite" database into GEOIP_COUNTRY_DB.

DB-IP publishes it monthly (CC BY 4.0, no account) at
https://download.db-ip.com/free/dbip-country-lite-YYYY-MM.mmdb.gz, ~4 MB
compressed, ~8 MB on disk. The file is replaced atomically and only after it
opens as a country database, so a failed download keeps the previous one.
"""

from __future__ import annotations

import gzip
import os
import tempfile
import time
import urllib.request
from datetime import date

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

URL = 'https://download.db-ip.com/free/dbip-country-lite-{month}.mmdb.gz'
MAX_BYTES = 64 * 1024 * 1024


def _months(today: date):
    """This month, then the previous one (the new file appears on the 1st)."""
    yield today.strftime('%Y-%m')
    prev = date(today.year - (today.month == 1), (today.month - 2) % 12 + 1, 1)
    yield prev.strftime('%Y-%m')


class Command(BaseCommand):
    help = 'Download the DB-IP country database used for the site-version hint.'
    # Runs from a postdeploy hook without the web environment; it needs no DB.
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument('--max-age-days', type=float, default=25,
                            help='Skip when the current file is younger (default 25).')
        parser.add_argument('--force', action='store_true')

    def handle(self, *args, **options):
        path = settings.GEOIP_COUNTRY_DB
        if not path:
            raise CommandError('GEOIP_COUNTRY_DB is empty.')
        try:
            age_days = (time.time() - os.stat(path).st_mtime) / 86400
        except OSError:
            age_days = None
        if not options['force'] and age_days is not None and age_days < options['max_age_days']:
            self.stdout.write(f'{path} is {age_days:.1f} days old; skip')
            return

        os.makedirs(os.path.dirname(path), exist_ok=True)
        errors = []
        for month in _months(date.today()):
            url = URL.format(month=month)
            try:
                self._install(url, path)
            except Exception as exc:  # noqa: BLE001 — try the previous month
                errors.append(f'{url}: {exc}')
                continue
            self.stdout.write(f'{path} <- {url}')
            return
        raise CommandError('; '.join(errors))

    def _install(self, url, path):
        import maxminddb

        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix='.geoip-', suffix='.mmdb')
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'interoves-geoip-sync'})
            with os.fdopen(fd, 'wb') as dst, urllib.request.urlopen(request, timeout=60) as response, \
                    gzip.GzipFile(fileobj=response) as src:
                copied = 0
                while chunk := src.read(1024 * 1024):
                    copied += len(chunk)
                    if copied > MAX_BYTES:
                        raise ValueError('database is larger than expected')
                    dst.write(chunk)
            with maxminddb.open_database(tmp) as reader:
                kind = reader.metadata().database_type
                if 'Country' not in kind:
                    raise ValueError(f'unexpected database type {kind!r}')
            os.chmod(tmp, 0o644)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
