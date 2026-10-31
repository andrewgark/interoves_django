"""Country of the visitor behind a request, for the site-version hint only.

The result is a two-letter ISO code or ``'unknown'``. It only decides whether
to suggest the other public hostname, never access, so a VPN or a missing
GeoIP database is fine: when the client address cannot be trusted the answer
is ``'unknown'`` rather than a guess.

Daphne runs without proxy headers behind the EB Nginx on the same instance,
so ``REMOTE_ADDR`` is loopback and ``X-Forwarded-For`` is the whole proxy
chain. Each hop appends the address it received the connection from, so the
chain is read from the right and only the shapes produced by our own proxies
are accepted:

interoves.com: browser → Cloudflare → ALB :80 → EB Nginx
    ``…, <client>, <Cloudflare edge>, <ALB private IP>``. The ALB :80 listener
    accepts only Cloudflare ranges, and the Cloudflare hop is checked against
    the published ranges, so ``CF-IPCountry`` (set by Cloudflare) is used,
    falling back to GeoIP of the client hop Cloudflare appended.

ru.interoves.com via the AWS gateway: browser → UltraVDS → Tailscale →
gateway → ALB :443 → EB Nginx
    ``<client>, <gateway public IP>, <ALB private IP>``. UltraVDS overwrites
    the header with its peer, the gateway passes it on, the ALB appends the
    gateway. The gateway has no fixed address, so this shape is accepted as
    is; a request sent straight to the ALB :443 listener can forge it, but
    only for its own hint.

ru.interoves.com via the fallback Tailscale node: browser → UltraVDS →
Tailscale → EB Nginx
    ``<client>, <UltraVDS Tailscale IP>`` (100.64.0.0/10).

Any other shape, or a client hop that is not a public address, is unknown.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import threading

from django.conf import settings

logger = logging.getLogger(__name__)

UNKNOWN = 'unknown'

INTL_HOST = 'interoves.com'
RU_HOST = 'ru.interoves.com'

# https://www.cloudflare.com/ips/ (also the ALB :80 security group).
CLOUDFLARE_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    '173.245.48.0/20', '103.21.244.0/22', '103.22.200.0/22', '103.31.4.0/22',
    '141.101.64.0/18', '108.162.192.0/18', '190.93.240.0/20', '188.114.96.0/20',
    '197.234.240.0/22', '198.41.128.0/17', '162.158.0.0/15', '104.16.0.0/13',
    '104.24.0.0/14', '172.64.0.0/13', '131.0.72.0/22',
    '2400:cb00::/32', '2606:4700::/32', '2803:f800::/32', '2405:b500::/32',
    '2405:8100::/32', '2a06:98c0::/29', '2c0f:f248::/32',
))
TAILSCALE_NETWORK = ipaddress.ip_network('100.64.0.0/10')


def _ip(value):
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _is_public(addr) -> bool:
    return addr is not None and addr.is_global


def _in_cloudflare(addr) -> bool:
    return addr is not None and any(addr in net for net in CLOUDFLARE_NETWORKS)


def _proxy_chain(request):
    """Parsed X-Forwarded-For hops, or None unless the peer is the local Nginx."""
    peer = _ip(request.META.get('REMOTE_ADDR') or '')
    if peer is None or not peer.is_loopback:
        return None
    raw = request.META.get('HTTP_X_FORWARDED_FOR') or ''
    hops = [_ip(part) for part in raw.split(',')] if raw.strip() else []
    if not hops or any(hop is None for hop in hops):
        return None
    return hops


def _country_code(value) -> str:
    code = (value or '').strip().upper()
    # XX: Cloudflare could not tell; T1: Tor exit.
    if len(code) == 2 and code.isalpha() and code not in ('XX', 'T1'):
        return code
    return UNKNOWN


def _intl_country(request, hops) -> str:
    if len(hops) < 3:
        return UNKNOWN
    client, edge, alb = hops[-3:]
    if not alb.is_private or not _in_cloudflare(edge):
        return UNKNOWN
    # Cloudflare's own verdict needs no client address (with Pseudo IPv4 the
    # hop is a 240.0.0.0/4 placeholder); the GeoIP fallback does.
    country = _country_code(request.META.get('HTTP_CF_IPCOUNTRY'))
    if country != UNKNOWN:
        return country
    return lookup_country(client) if _is_public(client) else UNKNOWN


def _ru_country(hops) -> str:
    if len(hops) == 3 and hops[2].is_private and _is_public(hops[1]):
        client = hops[0]
    elif len(hops) == 2 and hops[1] in TAILSCALE_NETWORK:
        client = hops[0]
    else:
        return UNKNOWN
    return lookup_country(client) if _is_public(client) else UNKNOWN


def visitor_country(request) -> str:
    """'RU', another ISO country code, or 'unknown'. Cached on the request."""
    cached = getattr(request, '_visitor_country', None)
    if cached is not None:
        return cached
    country = UNKNOWN
    hops = _proxy_chain(request)
    if hops is not None:
        host = request.get_host()
        if host == INTL_HOST:
            country = _intl_country(request, hops)
        elif host == RU_HOST:
            country = _ru_country(hops)
    request._visitor_country = country
    return country


class _GeoIPReader:
    """Lazily opened MMDB country database, reopened when the file changes."""

    def __init__(self):
        self._lock = threading.Lock()
        self._reader = None
        self._mtime = None
        self._path = None

    def _current(self):
        path = getattr(settings, 'GEOIP_COUNTRY_DB', '') or ''
        try:
            mtime = os.stat(path).st_mtime if path else None
        except OSError:
            mtime = None
        if mtime is None:
            return None
        if self._reader is not None and self._path == path and self._mtime == mtime:
            return self._reader
        with self._lock:
            if self._reader is None or self._path != path or self._mtime != mtime:
                import maxminddb

                try:
                    reader = maxminddb.open_database(path)
                except (OSError, ValueError, maxminddb.InvalidDatabaseError):
                    logger.warning('geoip: cannot open %s', path, exc_info=True)
                    return None
                # The old reader is left to the GC: another thread may still
                # be reading from it.
                self._reader, self._path, self._mtime = reader, path, mtime
        return self._reader

    def country(self, addr) -> str:
        reader = self._current()
        if reader is None:
            return UNKNOWN
        try:
            record = reader.get(str(addr)) or {}
        except (ValueError, OSError):
            return UNKNOWN
        country = record.get('country') or record.get('registered_country') or {}
        return _country_code(country.get('iso_code'))


_reader = _GeoIPReader()


def lookup_country(addr) -> str:
    return _reader.country(addr)
