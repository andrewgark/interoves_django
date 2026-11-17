"""Gzip text responses so the ALB → Cloudflare / RU-proxy hop is compressed.

Green's nginx keeps ``gzip off`` (its nginx.conf is protected from bundle
changes), so without this every HTML/JSON byte leaves AWS uncompressed and is
only compressed later by Cloudflare. Django 4.2's GZipMiddleware already pads
responses against BREACH; this subclass only limits it to text types, so
PDFs, images and event streams pass through untouched.
"""

from django.middleware.gzip import GZipMiddleware

_COMPRESSIBLE_PREFIXES = (
    'text/html',
    'text/plain',
    'text/css',
    'text/javascript',
    'text/xml',
    'application/json',
    'application/javascript',
    'application/xml',
    'application/manifest+json',
    'image/svg+xml',
)


def _is_compressible(response) -> bool:
    content_type = (response.get('Content-Type') or '').split(';', 1)[0].strip().lower()
    return content_type.startswith(_COMPRESSIBLE_PREFIXES)


class TextGZipMiddleware(GZipMiddleware):
    def process_response(self, request, response):
        if not _is_compressible(response):
            return response
        return super().process_response(request, response)
