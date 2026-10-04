# Censorly Wikipedia extract fixtures

Frozen slices of Russian Wikipedia `explaintext` extracts for offline regression
tests of `games.censorly.wiki.clean_wiki_extract` / `_trim_extract`.

Do not fetch live Wikipedia in CI for these cases — update files deliberately
when the cleaner’s contract changes.

| File | Source | What it covers |
|------|--------|----------------|
| `magnetism_math_raw.txt` | Магнетизм | MathML glyph dump + `{\displaystyle …}` |
| `magnetism_miller_raw.txt` | Магнетизм | Crystal directions `[100]`, `[010]`, `[001]` must survive |
| `water_editorial_raw.txt` | Вода | `[уточнить]` (+ injected `[12]` footnote) |
| `moscow_lead_raw.txt` | Москва | Lead `МФА: […]` pronunciation dump |
| `pool/*.txt` | 15 titles from `article_pool.txt` | First 50k chars of `explaintext` (whole article if shorter). Science with formulas plus Париж and Китай. See `pool/manifest.json`. |
| `leads/*.txt` | Толстой, Байкал, Медоносная пчела, Сатурн | First two prose paragraphs, fetched 2026-10-04. `*.endings.json` is the reviewed mask-tail snapshot. |
| `articles/*.txt` | Those four titles | Playable extract (tail sections removed, capped at 28k). `*.endings.json` is every content-token tail. Baikal’s vandalized paragraph about resolution № 234 is omitted. |
