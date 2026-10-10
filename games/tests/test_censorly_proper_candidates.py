from __future__ import annotations

import json
import gzip
import tempfile
from pathlib import Path
from unittest import TestCase

from games.censorly.lexical.tools.proper_candidates import (
    classify_eponym_candidate,
    extract_candidates,
    load_jsonl,
    run_experiment,
    write_reports,
)


def no_morphology(word: str) -> tuple[str, tuple[str, ...]]:
    return word.casefold(), ()


def closed(_left: str, _right: str) -> str:
    return ''


def entry(word: str, *, categories=None, derived=None, related=None, etymology_text='') -> dict:
    row = {
        'word': word,
        'lang': 'Russian',
        'pos': 'noun',
        'categories': categories or [],
        'senses': [],
    }
    if derived is not None:
        row['derived'] = derived
    if related is not None:
        row['related'] = related
    if etymology_text:
        row['etymology_text'] = etymology_text
    return row


class ProperCandidateTests(TestCase):
    def test_eponym_is_separate_and_never_auto_accepted(self):
        row = {
            'relation_type': 'etymological_derivation',
            'category': 'person',
            'status': 'confirmed',
            'source_article': 'Дизель',
            'derivative_article': 'дизелизация',
            'reason': 'Назван в честь Рудольфа Дизеля.',
        }
        relation, category, status, flags = classify_eponym_candidate(row)
        self.assertEqual((relation, category, status), ('eponym', 'acceptable_eponym', 'manual_review'))
        self.assertIn('needs_frequency_check', flags)

    def test_direct_derived_is_confirmed_and_pool_is_prioritized(self):
        rows = [
            entry('Абиджан', categories=['Русские имена собственные', 'Города'], derived=[{'word': 'абиджанский'}]),
            entry('абиджанский'),
        ]
        result = extract_candidates(
            rows, pool_titles=('Абиджан', 'абиджанский'),
            explain_func=closed, normalize_func=no_morphology,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].status, 'confirmed')
        self.assertEqual(result[0].relation_type, 'direct_derivation')
        self.assertTrue(result[0].article_pool_source)
        self.assertTrue(result[0].article_pool_derivative)

    def test_related_is_not_treated_as_derivation(self):
        rows = [
            entry('Дарвин', categories=['Фамилии'], related=['дарвинизм']),
            entry('дарвинизм'),
        ]
        result = extract_candidates(
            rows, explain_func=closed, normalize_func=no_morphology,
        )
        self.assertEqual(result[0].status, 'ambiguous')
        self.assertIn('related_is_not_proof', result[0].ambiguity_flags)

    def test_existing_game_edge_is_removed(self):
        rows = [entry('Абиджан', categories=['Города'], derived=['абиджанский']), entry('абиджанский')]
        result = extract_candidates(
            rows, explain_func=lambda left, right: 'proper', normalize_func=no_morphology,
        )
        self.assertEqual(result, [])

    def test_no_transitive_closure(self):
        rows = [
            entry('Дарвин', categories=['Фамилии'], derived=['дарвинизм']),
            entry('дарвинизм', derived=['дарвинистский']),
            entry('дарвинистский'),
        ]
        result = extract_candidates(rows, explain_func=closed, normalize_func=no_morphology)
        pairs = {(row.source_name, row.derivative) for row in result}
        self.assertIn(('дарвин', 'дарвинизм'), pairs)
        self.assertNotIn(('дарвинизм', 'дарвинистский'), pairs)
        self.assertNotIn(('дарвин', 'дарвинистский'), pairs)

    def test_omitted_fields_malformed_rows_and_multword_links_are_safe(self):
        rows = [
            {},
            entry('Венеция', categories=['Города'], derived=['венецианский', 'венецианская маска']),
            entry('венецианский'),
            {'word': 'broken', 'derived': [None, {}, 4]},
        ]
        result = extract_candidates(rows, explain_func=closed, normalize_func=no_morphology)
        self.assertEqual([(row.source_name, row.derivative) for row in result], [('венеция', 'венецианский')])

    def test_explicit_etymology_uses_only_direct_wiki_link(self):
        rows = [
            entry('гуглить', etymology_text='Происходит от названия компании [[Google]].'),
            entry('Google', categories=['English proper nouns', 'Companies']),
        ]
        result = extract_candidates(rows, explain_func=closed, normalize_func=no_morphology)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].relation_type, 'brand_derived')
        self.assertEqual(result[0].source_name, 'google')

    def test_jsonl_loader_skips_incomplete_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.jsonl'
            path.write_text('{"word":"Абиджан"}\nnot json\n[]\n\n', encoding='utf-8')
            self.assertEqual(list(load_jsonl(path)), [{'word': 'Абиджан'}])

    def test_gzip_and_real_wiktextract_field_names(self):
        rows = [
            {
                'word': 'гуглить', 'lang_code': 'ru', 'lang': 'Русский', 'pos': 'verb',
                'etymology_texts': ['Происходит от названия компании.'],
                'etymology_links': [['Google', 'Google']],
            },
            {
                'word': 'Google', 'lang_code': 'ru', 'lang': 'Русский', 'pos': 'name',
                'categories': ['Companies', 'Русские имена собственные'],
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'sample.jsonl.gz'
            with gzip.open(path, 'wt', encoding='utf-8') as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + '\n')
            candidates, stats = run_experiment(
                path, pool_titles=('Google', 'гуглить'),
                explain_func=closed, normalize_func=no_morphology,
            )
            self.assertEqual(stats.records_total, 2)
            self.assertEqual(stats.russian_records, 2)
            self.assertEqual(len(candidates), 1)
            self.assertEqual(candidates[0].source_name, 'google')
            self.assertEqual(candidates[0].relation_type, 'brand_derived')

    def test_reports_are_machine_readable_and_tabular(self):
        rows = [entry('Абиджан', categories=['Города'], derived=['абиджанский']), entry('абиджанский')]
        result = extract_candidates(rows, explain_func=closed, normalize_func=no_morphology)
        with tempfile.TemporaryDirectory() as directory:
            jsonl = Path(directory) / 'candidates.jsonl'
            tsv = Path(directory) / 'candidates.tsv'
            write_reports(result, jsonl, tsv)
            record = json.loads(jsonl.read_text(encoding='utf-8').splitlines()[0])
            self.assertEqual(record['status'], 'confirmed')
            self.assertEqual(tsv.read_text(encoding='utf-8').splitlines()[0].split('\t')[0], 'source_name')
