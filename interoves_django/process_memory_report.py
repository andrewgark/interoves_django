"""Periodic memory breakdown for a long-running ASGI process.

Green Daphne grows by tens of megabytes per hour until the instance runs out
of RAM. One log line every few minutes separates the usual causes:

- ``rss_anon_mb`` rising while ``py_blocks`` is flat points at the C heap
  (glibc arena fragmentation or a C extension), not at Python objects;
- both rising together points at Python objects, and ``lru`` shows whether
  the module-level ``functools.lru_cache`` tables are the ones growing.

``malloc_arena_max`` echoes the glibc setting from the Procfile, so series
from before and after an allocator change are told apart.

``INTEROVES_TRACEMALLOC=1`` additionally starts ``tracemalloc`` at import and
logs the source lines whose allocations grew most since the previous report.
It costs memory and CPU, so it is meant for a short diagnostic window only.
"""

from __future__ import annotations

import functools
import gc
import logging
import os
import sys
import tracemalloc


logger = logging.getLogger("application")

PROJECT_MODULE_PREFIXES = ("games.", "interoves_django.", "iam.", "ads.", "microsites.")
LRU_REPORT_LIMIT = 12
TRACEMALLOC_TOP = 15
TRACEMALLOC_ENABLED = os.environ.get("INTEROVES_TRACEMALLOC", "").strip() == "1"

if TRACEMALLOC_ENABLED and not tracemalloc.is_tracing():
    tracemalloc.start(1)


def _proc_status_mb(path="/proc/self/status"):
    fields = {}
    try:
        with open(path, encoding="ascii") as status:
            for line in status:
                key, _sep, value = line.partition(":")
                if key in {"VmRSS", "RssAnon", "RssFile"}:
                    fields[key] = int(value.split()[0]) / 1024
    except (OSError, ValueError):
        pass
    return fields


def lru_cache_sizes(modules=None):
    """``{"module.function": currsize}`` for project-level lru caches."""
    sizes = {}
    for name, module in list((sys.modules if modules is None else modules).items()):
        if module is None or not name.startswith(PROJECT_MODULE_PREFIXES):
            continue
        for attr, value in list(vars(module).items()):
            if not isinstance(value, functools._lru_cache_wrapper):
                continue
            # Re-exported names point at the same wrapper; count it once.
            if getattr(value, "__module__", name) != name:
                continue
            size = value.cache_info().currsize
            if size:
                sizes[f"{name}.{attr}"] = size
    return sizes


class ProcessMemoryReporter:
    def __init__(self):
        self._previous_snapshot = None

    def report(self, *, pid, instance):
        status = _proc_status_mb()
        sizes = lru_cache_sizes()
        top = sorted(sizes.items(), key=lambda item: item[1], reverse=True)
        logger.info(
            "asgi_memory pid=%s instance=%s rss_mb=%.0f rss_anon_mb=%.0f "
            "rss_file_mb=%.0f py_blocks=%s gc_counts=%s malloc_arena_max=%s "
            "lru_entries=%s lru=%s",
            pid,
            instance,
            status.get("VmRSS", 0),
            status.get("RssAnon", 0),
            status.get("RssFile", 0),
            sys.getallocatedblocks(),
            "/".join(str(count) for count in gc.get_count()),
            os.environ.get("MALLOC_ARENA_MAX", "default"),
            sum(sizes.values()),
            ",".join(f"{name}={size}" for name, size in top[:LRU_REPORT_LIMIT]),
        )
        if tracemalloc.is_tracing():
            self._report_tracemalloc(pid=pid, instance=instance)

    def _report_tracemalloc(self, *, pid, instance):
        snapshot = tracemalloc.take_snapshot().filter_traces((
            tracemalloc.Filter(False, tracemalloc.__file__),
        ))
        previous, self._previous_snapshot = self._previous_snapshot, snapshot
        if previous is None:
            stats = snapshot.statistics("lineno")[:TRACEMALLOC_TOP]
            lines = [f"{stat.traceback[0]} size_kb={stat.size / 1024:.0f}" for stat in stats]
        else:
            stats = snapshot.compare_to(previous, "lineno")[:TRACEMALLOC_TOP]
            lines = [
                f"{stat.traceback[0]} size_kb={stat.size / 1024:.0f} "
                f"diff_kb={stat.size_diff / 1024:+.0f}"
                for stat in stats
            ]
        traced, peak = tracemalloc.get_traced_memory()
        logger.info(
            "asgi_tracemalloc pid=%s instance=%s traced_mb=%.0f peak_mb=%.0f top=%s",
            pid,
            instance,
            traced / 2**20,
            peak / 2**20,
            " | ".join(lines),
        )
