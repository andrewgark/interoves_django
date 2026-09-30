#!/usr/bin/env bash
# Copy the nutrimatic-ru runtime bundle into this repo before `eb deploy`.
# Eurovision booklet assets are served through the microsites lazy cache and
# are intentionally not copied into the shared static tree.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NUTRIMATIC_SRC="${NUTRIMATIC_SRC:-$HOME/nutrimatic-ru}"

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

echo "Skipping Eurovision booklet copy: served through the lazy microsites cache."
