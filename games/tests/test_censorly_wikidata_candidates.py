"""Tests for the offline Wikidata candidate tool."""

import json
import tempfile
from pathlib import Path

from django.test import SimpleTestCase

from games.puzzles.censorly.lexical.tools.candidates.wikidata_candidates import (
    _lexeme_rows,
    _p1549_rows,
    build_candidates,
    read_jsonl,
)


class WikidataCandidateParsingTests(SimpleTestCase):
    def test_p1549_parser_keeps_qid_language_and_class(self):
        payload = {
            "results": {
                "bindings": [{
                    "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q159"},
                    "itemLabel": {"xml:lang": "ru", "value": "Россия"},
                    "demonym": {"xml:lang": "ru", "value": "россиянин"},
                }]
            }
        }
        rows = list(_p1549_rows(
            payload, class_qid="Q6256", class_name="countries", query="SELECT",
        ))
        self.assertEqual(rows[0]["item_qid"], "Q159")
        self.assertEqual(rows[0]["demonym_language"], "ru")
        self.assertEqual(rows[0]["class_qid"], "Q6256")

    def test_lexeme_parser_keeps_lids_and_lemmas(self):
        payload = {
            "results": {
                "bindings": [{
                    "base": {"type": "uri", "value": "http://www.wikidata.org/entity/L1"},
                    "baseLemma": {"xml:lang": "ru", "value": "Россия"},
                    "derived": {"type": "uri", "value": "http://www.wikidata.org/entity/L2"},
                    "derivedLemma": {"xml:lang": "ru", "value": "российский"},
                }]
            }
        }
        rows = list(_lexeme_rows(payload, query="SELECT"))
        self.assertEqual(rows[0]["base_lid"], "L1")
        self.assertEqual(rows[0]["derived_lid"], "L2")
        self.assertEqual(rows[0]["derived_lemma"], "российский")

    def test_jsonl_reader_rejects_non_object(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.jsonl"
            path.write_text("[1]\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_jsonl(path)

    def test_candidate_builder_rejects_multitoken_and_self_pairs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.jsonl"
            rows = [
                {
                    "item_label": "Россия",
                    "demonym": "россиянин",
                    "item_qid": "Q159",
                    "class_qid": "Q6256",
                    "class_name": "countries",
                },
                {
                    "item_label": "Новая Зеландия",
                    "demonym": "новозеландец",
                    "item_qid": "Q664",
                    "class_qid": "Q6256",
                    "class_name": "countries",
                },
                {
                    "item_label": "Россия",
                    "demonym": "Россия",
                    "item_qid": "Q159",
                    "class_qid": "Q6256",
                    "class_name": "countries",
                },
            ]
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            candidates = build_candidates(path)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["source_name"], "Россия")
        self.assertEqual(candidates[0]["decision"], "pending_matcher")

    def test_candidate_builder_deduplicates_inflected_demonyms_by_lemma(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "raw.jsonl"
            rows = [
                {
                    "item_label": "Россия",
                    "demonym": "россиянин",
                    "item_qid": "Q159",
                    "class_qid": "Q6256",
                    "class_name": "countries",
                },
                {
                    "item_label": "Россия",
                    "demonym": "россияне",
                    "item_qid": "Q159",
                    "class_qid": "Q6256",
                    "class_name": "countries",
                },
            ]
            path.write_text(
                "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                encoding="utf-8",
            )
            candidates = build_candidates(path)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["normalized_derivative"], "россиянин")
        self.assertEqual(candidates[0]["raw_evidence_count"], 2)
        self.assertEqual(candidates[0]["derivative_variants"], "россияне,россиянин")
