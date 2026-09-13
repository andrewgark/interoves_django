#!/usr/bin/env python
"""node --check for <script> blocks embedded in Django templates.

Django tags are neutralised into JS literals first, so this catches real syntax
errors in large inline scripts (static/templates/new/task_group.html) that no
other check in the repo looks at.

Usage: python scripts/check_inline_js_syntax.py <template> [<template> ...]
"""
import os
import re
import subprocess
import sys
import tempfile

SCRIPT_BLOCK = re.compile(r'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.S)
IF_BLOCK = re.compile(r'\{%\s*if\b.*?%\}.*?\{%\s*endif\s*%\}', re.S)
ANY_TAG = re.compile(r'\{%.*?%\}', re.S)
VARIABLE = re.compile(r'\{\{.*?\}\}', re.S)


def neutralise(js):
    previous = None
    while previous != js:
        previous = js
        js = IF_BLOCK.sub('0', js)
    js = ANY_TAG.sub('', js)
    return VARIABLE.sub('0', js)


def check(path):
    with open(path, encoding='utf-8') as handle:
        source = handle.read()
    failures = 0
    for index, block in enumerate(SCRIPT_BLOCK.findall(source)):
        if not block.strip():
            continue
        tmp = tempfile.NamedTemporaryFile(
            'w', suffix='.js', delete=False, encoding='utf-8',
        )
        tmp.write(neutralise(block))
        tmp.close()
        try:
            result = subprocess.run(
                ['node', '--check', tmp.name], capture_output=True, text=True,
            )
        finally:
            os.unlink(tmp.name)
        if result.returncode:
            failures += 1
            sys.stderr.write(
                '{}: inline <script> #{} failed node --check\n{}\n'.format(
                    path, index, result.stderr,
                )
            )
    return failures


def main(argv):
    if not argv:
        sys.stderr.write(__doc__)
        return 2
    failures = sum(check(path) for path in argv)
    if failures:
        return 1
    print('check_inline_js_syntax: ok ({} template(s))'.format(len(argv)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
