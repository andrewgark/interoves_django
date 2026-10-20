import functools
import tracemalloc
import types
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from interoves_django.process_memory_report import (
    ProcessMemoryReporter,
    lru_cache_sizes,
)


def _module_with_cache(name):
    module = types.ModuleType(name)

    @functools.lru_cache(maxsize=None)
    def lookup(value):
        return value

    lookup.__module__ = name
    module.lookup = lookup
    return module


class LruCacheSizesTests(TestCase):
    def test_reports_project_caches_once_and_skips_other_packages(self):
        project = _module_with_cache('games.fake_cache')
        for value in range(3):
            project.lookup(value)
        reexport = types.ModuleType('games.fake_reexport')
        reexport.lookup = project.lookup
        library = _module_with_cache('pymorphy_fake')
        library.lookup(1)

        sizes = lru_cache_sizes({
            'games.fake_cache': project,
            'games.fake_reexport': reexport,
            'pymorphy_fake': library,
        })

        self.assertEqual(sizes, {'games.fake_cache.lookup': 3})


    def test_empty_module_mapping_reports_nothing(self):
        self.assertEqual(lru_cache_sizes({}), {})


class ProcessMemoryReporterTests(TestCase):
    def test_report_logs_one_line_with_rss_and_cache_sizes(self):
        with self.assertLogs('application', level='INFO') as logs:
            ProcessMemoryReporter().report(pid=1, instance='test')

        self.assertEqual(len(logs.output), 1)
        self.assertIn('asgi_memory pid=1 instance=test rss_mb=', logs.output[0])
        self.assertIn('py_blocks=', logs.output[0])

    def test_report_shows_malloc_arena_setting(self):
        with patch.dict('os.environ', {'MALLOC_ARENA_MAX': '2'}), \
                self.assertLogs('application', level='INFO') as logs:
            ProcessMemoryReporter().report(pid=1, instance='test')

        self.assertIn('malloc_arena_max=2 ', logs.output[0])

    def test_procfile_limits_glibc_malloc_arenas_for_daphne(self):
        procfile = (Path(__file__).resolve().parents[2] / 'Procfile').read_text()

        self.assertRegex(procfile, r'^web: env MALLOC_ARENA_MAX=2 daphne ')

    def test_tracemalloc_report_compares_with_previous_snapshot(self):
        reporter = ProcessMemoryReporter()
        tracemalloc.start(1)
        try:
            with self.assertLogs('application', level='INFO') as logs:
                reporter.report(pid=1, instance='test')
                kept = [bytearray(1024) for _ in range(100)]
                reporter.report(pid=1, instance='test')
        finally:
            tracemalloc.stop()

        tracemalloc_lines = [line for line in logs.output if 'asgi_tracemalloc' in line]
        self.assertEqual(len(tracemalloc_lines), 2)
        self.assertIn('diff_kb=', tracemalloc_lines[1])
        self.assertTrue(kept)
