#!/usr/bin/env bash
# Preferred path for a prod-safe find-expr: build inside amazonlinux:2023 (same glibc family
# as EB "Python 3.12 on 64bit Amazon Linux 2023"), then stage into nutrimatic_bundle/ for deploy.
#
# Avoids: compiling on the live EB instance (CPU/RAM/disk vs gunicorn; SSH drops; outages).
#
# Prerequisites: Docker, NUTRIMATIC_SRC (default ~/nutrimatic-ru) with conanfile.py.
#
# Usage (repo root):
#   ./scripts/stage_nutrimatic_find_expr_docker_al2023.sh
#   NUTRIMATIC_SRC=/path/to/nutrimatic-ru ./scripts/stage_nutrimatic_find_expr_docker_al2023.sh
#
# Next: ./deploy.sh (or your usual eb deploy). Index remains on S3 / env vars as before.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

"${REPO_ROOT}/scripts/build_nutrimatic_find_expr_amazonlinux2023.sh"
"${REPO_ROOT}/scripts/bundle_microsites.sh"

OUT="${REPO_ROOT}/nutrimatic_bundle/build/find-expr"
if [[ ! -x "$OUT" ]]; then
  echo "Expected executable at $OUT" >&2
  exit 1
fi

echo "OK: staged $OUT ($(wc -c < "$OUT") bytes)"
echo "Next: deploy (e.g. ./deploy.sh) so EB picks up nutrimatic_bundle/build/find-expr"
