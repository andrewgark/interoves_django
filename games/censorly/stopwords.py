"""Russian function-word list: prepositions, conjunctions, particles."""

from __future__ import annotations

from games.censorly.normalize import normalize_surface

# Предлоги, союзы, частицы. Нормализованы ё→е.
# Местоимения сюда не входят: он, нем, себя, этот, кто, чей, все.
# «что» и «чтобы» остаются как союзы, «то» — как союз и частица
# («то… то», «а то»), хотя у этих форм есть местоименный омоним.
_STOP_RAW = """
а без благодаря будто бы в вдоль вместо вне внутрь возле вот впрочем
всё-таки всюду где да даже для до ещё еще
ж же за затем зато и из из-за из-под или либо именно иначе к как
как-то когда ко кроме куда ли лишь между меж мимо на над надо
напротив наподобие насчёт насчет не нет ни
ниже но ну о об обо около от ото
относительно перед по под подо подобно помимо поперёк поперек после
потому почему почти при про против ради разве с сверх
среди судя так также там то тогда тоже только туда у уж
уже хотя через что чтобы чуть эдак
""".split()

STOP_WORDS = frozenset(normalize_surface(w) for w in _STOP_RAW if w.strip())
# Mathematical operators are structural, always-visible tokens just like
# punctuation. Keeping them here documents their open-by-default semantics.
STOP_SYMBOLS = frozenset(
    '∫ ∬ ∭ ∮ ≤ ≥ ≠ ≈ ∈ ∉ ∑ ∏ ∂ ∇ × · ± ∓ → ← ↔ ⟶ ⟵ ⟷ ⇒ ⇐ ⇔ ⟹ ⟸ ⟺ ↦ '
    '⟨ ⟩ ∪ ∩ ∧ ∨ √ ∞ △ ⊥ ∥ ⊗ ∼ ⊆ ∣ ∠ ′ '
    'ᵃ ᵇ ᶜ ᵈ ᵉ ᶠ ᵍ ʰ ⁱ ʲ ᵏ ˡ ᵐ ⁿ ᵒ ᵖ ʳ ˢ ᵗ ᵘ ᵛ ʷ ˣ ʸ ᶻ'
    .split()
)


def is_stop_word(surface: str) -> bool:
    normalized = normalize_surface(surface)
    return normalized in STOP_WORDS or normalized in STOP_SYMBOLS
