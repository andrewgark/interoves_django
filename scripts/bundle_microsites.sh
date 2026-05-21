#!/usr/bin/env bash
# Copy nutrimatic-ru runtime bundle and Eurovision booklet PDFs + HTML into this repo
# before `eb deploy`. Override sources with env vars.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NUTRIMATIC_SRC="${NUTRIMATIC_SRC:-$HOME/nutrimatic-ru}"
BOOKLET_SRC="${BOOKLET_SRC:-$HOME/eurovision2026booklet/dist}"

DEST_NUT="$ROOT/nutrimatic_bundle"
if [[ -d "$NUTRIMATIC_SRC" ]]; then
  mkdir -p "$DEST_NUT/build"
  if [[ -f "$NUTRIMATIC_SRC/build/find-expr" ]]; then
    install -m0755 "$NUTRIMATIC_SRC/build/find-expr" "$DEST_NUT/build/find-expr"
  else
    echo "Warning: $NUTRIMATIC_SRC/build/find-expr not found"
  fi
  # Only copy merged index (~350MB), never ruwiki.* shards (tens of GB). Prefer S3 on EB — see .ebignore.
  _idx_src="${NUTRIMATIC_BUNDLE_INDEX_SRC:-}"
  if [[ -z "$_idx_src" && -f "$NUTRIMATIC_SRC/wiki-merged.index" ]]; then
    _idx_src="$NUTRIMATIC_SRC/wiki-merged.index"
  fi
  if [[ -n "$_idx_src" && -f "$_idx_src" ]]; then
    install -m0644 "$_idx_src" "$DEST_NUT/$(basename "$_idx_src")"
  else
    echo "Note: no local merged index copied — set NUTRIMATIC_INDEX_S3_BUCKET/KEY on EB (or NUTRIMATIC_BUNDLE_INDEX_SRC)."
  fi
  if [[ -d "$NUTRIMATIC_SRC/cgi_scripts" ]]; then
    rm -rf "$DEST_NUT/cgi_scripts"
    cp -a "$NUTRIMATIC_SRC/cgi_scripts" "$DEST_NUT/"
  fi
  echo "Nutrimatic bundle updated under nutrimatic_bundle/"
else
  echo "Skip nutrimatic: $NUTRIMATIC_SRC not found"
fi

DEST_PDF="$ROOT/static/microsites/eurovision_booklet/2026"
BOOKLET_HTML_SRC="${BOOKLET_HTML_SRC:-$BOOKLET_SRC/html}"
if [[ -d "$BOOKLET_SRC" ]]; then
  mkdir -p "$DEST_PDF"
  # Mirror dist/*.pdf exactly (remove stale names no longer produced by the booklet build).
  rm -f "$DEST_PDF"/*.pdf
  shopt -s nullglob
  for pdf in "$BOOKLET_SRC"/*.pdf; do
    install -m0644 "$pdf" "$DEST_PDF/"
  done
  shopt -u nullglob
  echo "Booklet PDFs copied to static/microsites/eurovision_booklet/2026/"
else
  echo "Skip booklet PDFs: $BOOKLET_SRC not found"
fi
if [[ -d "$BOOKLET_HTML_SRC" ]]; then
  mkdir -p "$DEST_PDF/html"
  rm -rf "$DEST_PDF/html"/*
  cp -a "$BOOKLET_HTML_SRC"/. "$DEST_PDF/html/"
  echo "Booklet HTML bundles copied to static/microsites/eurovision_booklet/2026/html/"
else
  echo "Skip booklet HTML: $BOOKLET_HTML_SRC not found"
fi
BOOKLET_REPO="${BOOKLET_REPO:-$(cd "$BOOKLET_SRC/.." 2>/dev/null && pwd || true)}"
if [[ -n "${BOOKLET_REPO:-}" && -d "$BOOKLET_REPO/assets" ]]; then
  mkdir -p "$DEST_PDF/assets"
  rm -rf "$DEST_PDF/assets"/*
  cp -a "$BOOKLET_REPO/assets"/. "$DEST_PDF/assets/"
  echo "Booklet shared assets copied to static/microsites/eurovision_booklet/2026/assets/"
else
  echo "Skip booklet assets: $BOOKLET_REPO/assets not found"
fi
