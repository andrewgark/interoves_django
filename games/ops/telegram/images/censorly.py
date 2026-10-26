"""Site screenshot used by daily Censorly social posts."""

from __future__ import annotations

from django.conf import settings

from games.sections.paths import section_play_path
from games.ops.telegram.links import site_base_url
from games.ops.telegram.images.ladder import screenshot_page_element_png

_MASKED_TITLE_SCRIPT = r'''() => {
  const title = document.querySelector('#censorly-title');
  if (!title) return '';
  const maskWords = (text) => (text || '').replace(/[\p{L}\p{N}]+/gu, word => '█'.repeat(Array.from(word).length));
  return Array.from(title.childNodes).map(node => {
    if (node.nodeType !== Node.ELEMENT_NODE) return maskWords(node.textContent);
    const element = node;
    if (element.classList.contains('censorly-tok--mask')) {
      return '█'.repeat(Math.max(1, Number(element.dataset.len) || 1));
    }
    if (element.classList.contains('censorly-tok--revealed')) {
      return maskWords(element.dataset.surface || element.textContent);
    }
    return maskWords(element.textContent);
  }).join('');
}'''


def censorly_screenshot_url(number: int | str = 1) -> str:
    path = section_play_path('censorly', number)
    return '{}{}'.format(site_base_url().rstrip('/'), path)


def render_censorly_teaser_png(
    *,
    number: int | str,
    url: str | None = None,
    captured_title: list[str] | None = None,
) -> bytes:
    """Capture the article title and opening view, forcing visible mask endings."""
    if not getattr(settings, 'TELEGRAM_LADDER_SCREENSHOT', True):
        raise RuntimeError('Real Censorly screenshot is required for social posts')
    target = url or censorly_screenshot_url(number)
    return screenshot_page_element_png(
        target,
        ('#censorly-root .censorly__article',),
        viewport_width=1100,
        extra_css='''
          #censorly-root #censorly-fetched,
          #censorly-root #censorly-opened-words {
            display: none !important;
          }
          #censorly-root .censorly__article {
            max-height: 1100px !important;
            overflow: hidden !important;
          }
          #censorly-root.censorly .censorly-tok__ending {
            display: inline !important;
          }
        ''',
        wait_for_selector='#censorly-title .censorly-tok',
        require_selector='#censorly-root .censorly__article',
        capture_text_script=_MASKED_TITLE_SCRIPT if captured_title is not None else '',
        capture_text_out=captured_title,
    )
