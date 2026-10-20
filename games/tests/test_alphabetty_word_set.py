import random
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from games.alphabetty.core import max_word_length, normalize_word
from games.alphabetty.dicts import (
    VALID_PACKED_GZ,
    load_valid_set,
    read_packed_word_set,
    write_packed_word_set,
)
from games.alphabetty.word_set import PackedWordSet


class PackedWordSetTests(TestCase):
    def test_from_words_deduplicates_sorts_and_packs(self):
        words = PackedWordSet.from_words(['КОТ', 'АБА', 'КОТ', 'ЯК'])

        self.assertEqual(words.to_bytes(), '\nАБА\nКОТ\nЯК\n'.encode('cp1251'))
        self.assertEqual(len(words), 3)
        self.assertEqual(list(words), ['АБА', 'КОТ', 'ЯК'])

    def test_membership_rejects_neighbours_prefixes_and_extensions(self):
        words = PackedWordSet.from_words(['КОТ', 'КОТЁНОК', 'МАК'])

        for present in ('КОТ', 'КОТЁНОК', 'МАК'):
            self.assertIn(present, words)
        for absent in ('А', 'КО', 'КОТЫ', 'КОТЁН', 'ЛУК', 'МАКИ', 'ЯЯ', 'кот'):
            self.assertNotIn(absent, words)

    def test_membership_of_unsupported_values_is_false(self):
        words = PackedWordSet.from_words(['КОТ'])

        for value in ('', 'КОТ\n', '\nКОТ', '猫', 'КОТ🐈', None, 1, b'KOT'):
            self.assertNotIn(value, words)

    def test_empty_and_single_word_sets(self):
        empty = PackedWordSet()
        self.assertEqual(len(empty), 0)
        self.assertEqual(list(empty), [])
        self.assertNotIn('КОТ', empty)
        self.assertEqual(empty.max_length, 0)
        self.assertEqual(PackedWordSet.from_words([]).to_bytes(), b'\n')

        single = PackedWordSet.from_words(['КОТ'])
        self.assertIn('КОТ', single)
        self.assertNotIn('КОС', single)
        self.assertNotIn('КОУ', single)
        self.assertEqual(single.max_length, 3)

    def test_matches_builtin_set_on_random_words(self):
        rng = random.Random(20261020)
        alphabet = 'АБВЕКОЯ'

        def word():
            return ''.join(rng.choice(alphabet) for _ in range(rng.randint(1, 6)))

        reference = {word() for _ in range(3000)}
        packed = PackedWordSet.from_words(reference)

        self.assertEqual(len(packed), len(reference))
        self.assertEqual(set(packed), reference)
        probes = {word() for _ in range(5000)}
        probes |= {w[:-1] for w in reference if len(w) > 1}
        probes |= {w + 'А' for w in reference}
        for probe in probes:
            self.assertEqual(probe in packed, probe in reference, probe)

    def test_behaves_as_abstract_set(self):
        words = PackedWordSet.from_words(['АБА', 'КОТ'])

        self.assertEqual(words, frozenset({'АБА', 'КОТ'}))
        self.assertNotEqual(words, frozenset({'АБА'}))
        self.assertEqual(words & {'КОТ', 'ЯК'}, {'КОТ'})
        self.assertTrue(words <= {'АБА', 'КОТ', 'ЯК'})

    def test_equality_between_packed_sets_compares_buffers(self):
        words = PackedWordSet.from_words(['АБА', 'КОТ'])

        with patch.object(PackedWordSet, '__contains__', side_effect=AssertionError):
            self.assertEqual(words, PackedWordSet.from_words(['КОТ', 'АБА']))
            self.assertNotEqual(words, PackedWordSet.from_words(['АБА']))
        with self.assertRaises(TypeError):
            hash(words)

    def test_max_length_is_correct_across_scan_chunks(self):
        words = PackedWordSet.from_words(['А', 'БВ', 'ВГДЕЖЗ', 'Е', 'ЖЖЖЖ'])

        with patch('games.alphabetty.word_set._SCAN_CHUNK', 1):
            self.assertEqual(words.max_length, 6)
        self.assertEqual(PackedWordSet(words.to_bytes()).max_length, 6)

    def test_round_trips_through_bytes(self):
        words = PackedWordSet.from_words(['ЯК', 'АБА'])

        self.assertEqual(PackedWordSet(words.to_bytes()), words)

    def test_rejects_malformed_buffers(self):
        for blob in (b'', 'КОТ\n'.encode('cp1251'), '\nКОТ'.encode('cp1251'),
                     '\nАБА\n\nКОТ\n'.encode('cp1251')):
            with self.subTest(blob=blob), self.assertRaises(ValueError):
                PackedWordSet(blob)

    def test_from_words_rejects_words_it_cannot_store(self):
        for bad in ('', 'КО\nТ', '猫'):
            with self.subTest(word=bad), self.assertRaises(ValueError):
                PackedWordSet.from_words(['КОТ', bad])


class PackedDictionaryFileTests(TestCase):
    def test_write_and_read_round_trip_deterministically(self):
        words = PackedWordSet.from_words(['КОТ', 'АБА'])
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'a.gz', Path(tmp) / 'b.gz'
            write_packed_word_set(first, words)
            write_packed_word_set(second, words)

            self.assertEqual(read_packed_word_set(first), words)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_shipped_dictionary_is_canonical_and_normalized(self):
        words = read_packed_word_set(VALID_PACKED_GZ)
        lines = words.to_bytes()[1:-1].split(b'\n')

        self.assertGreater(len(words), 1_000_000)
        self.assertEqual(len(lines), len(words))
        self.assertTrue(all(a < b for a, b in zip(lines, lines[1:])))
        sample = [line.decode('cp1251') for line in lines[::997]]
        self.assertTrue(all(normalize_word(w) == w for w in sample))
        for known in ('ГОД', 'АВСТРАЛИЯ', 'КРАСИВАЯ'):
            self.assertIn(known, words)

    def test_valid_set_and_max_word_length_use_packed_dictionary(self):
        valid = load_valid_set()

        self.assertIsInstance(valid, PackedWordSet)
        self.assertEqual(max_word_length(), valid.max_length)
        self.assertEqual(max_word_length(frozenset({'КОТ', 'АБАЖУР'})), 6)
