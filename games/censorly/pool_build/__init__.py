"""Build a large curated article pool for Цензурки."""

from pathlib import Path

POOL_BUILD_DIR = Path(__file__).resolve().parent
DEFAULT_CACHE_DIR = Path(__file__).resolve().parents[3] / 'var' / 'censorly_pool'
ARTICLE_POOL_PATH = Path(__file__).resolve().parent.parent / 'article_pool.txt'
# Durable review outcomes for future harvests (repo-tracked).
ARTICLE_POOL_ALLOW_PATH = Path(__file__).resolve().parent.parent / 'article_pool_allow.txt'
ARTICLE_POOL_DENY_PATH = Path(__file__).resolve().parent.parent / 'article_pool_deny.txt'

MIN_WIKITEXT_BYTES = 8000
MIN_LANGLINKS = 20
LANGLINK_PROBE = 21
BATCH_SIZE = 40
USER_AGENT = 'InterovesCensorlyPoolBuilder/1.0 (https://interoves.com; article pool builder)'
WIKI_API = 'https://ru.wikipedia.org/w/api.php'
PAGEVIEWS_TOP = 'https://wikimedia.org/api/rest_v1/metrics/pageviews/top/ru.wikipedia/all-access'

VITAL_1000_PAGE = (
    'Википедия:Список_статей,_которые_должны_быть_во_всех_языковых_версиях/Статьи'
)
VITAL_10K_INDEX = (
    'Википедия:Список_статей,_которые_должны_быть_во_всех_языковых_версиях/10000'
)

# Cap shares of the target pool size (defaults assume target=10000).
THEME_BUCKET_CAPS = {
    'people': 0.25,
    'geography': 0.15,
    'history': 0.12,
    'stem': 0.28,
    'society': 0.15,
    'fill': 0.15,
}

THEME_TO_BUCKET = {
    'Люди': 'people',
    'География': 'geography',
    'История': 'history',
    'Естественные науки': 'stem',
    'Биология и медико-санитарные дисциплины': 'stem',
    'Математика': 'stem',
    'Технические и прикладные науки': 'stem',
    'Общество и общественные науки': 'society',
    'Гуманитарные науки': 'society',
    'Философия и религия': 'society',
    'Антропология, психология и повседневная жизнь': 'society',
    'Система мер': 'society',
}
