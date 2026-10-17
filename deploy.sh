#!/usr/bin/env bash
# Release the committed repository snapshot to the production Green ALB.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

DEPLOY_MODE="--deploy"
PREBUILT_RELEASE_ARTIFACT="${PREBUILT_RELEASE_ARTIFACT:-}"
PREBUILT_RELEASE_SHA256="${PREBUILT_RELEASE_SHA256:-}"
PREBUILT_RELEASE_COMMIT="${PREBUILT_RELEASE_COMMIT:-}"
MODE_SET=0
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --dry-run|--deploy)
      [[ "$MODE_SET" == "0" ]] || { echo "Choose exactly one of --dry-run and --deploy." >&2; exit 2; }
      DEPLOY_MODE="$1"; MODE_SET=1; shift
      ;;
    --prebuilt-artifact)
      [[ "$#" -ge 2 && -z "$PREBUILT_RELEASE_ARTIFACT" ]] || { echo "--prebuilt-artifact requires one path and may be set only once." >&2; exit 2; }
      PREBUILT_RELEASE_ARTIFACT="$2"; shift 2
      ;;
    --sha256)
      [[ "$#" -ge 2 && -z "$PREBUILT_RELEASE_SHA256" ]] || { echo "--sha256 requires one value and may be set only once." >&2; exit 2; }
      PREBUILT_RELEASE_SHA256="$2"; shift 2
      ;;
    --source-commit)
      [[ "$#" -ge 2 && -z "$PREBUILT_RELEASE_COMMIT" ]] || { echo "--source-commit requires one value and may be set only once." >&2; exit 2; }
      PREBUILT_RELEASE_COMMIT="$2"; shift 2
      ;;
    *) echo "Usage: $0 [--dry-run|--deploy] [--prebuilt-artifact ZIP --sha256 HEX --source-commit SHA]" >&2; exit 2 ;;
  esac
done

if [[ -n "$PREBUILT_RELEASE_ARTIFACT" || -n "$PREBUILT_RELEASE_SHA256" || -n "$PREBUILT_RELEASE_COMMIT" ]]; then
  [[ -n "$PREBUILT_RELEASE_ARTIFACT" && -n "$PREBUILT_RELEASE_SHA256" && -n "$PREBUILT_RELEASE_COMMIT" ]] || {
    echo "Exact-artifact mode requires --prebuilt-artifact, --sha256, and --source-commit." >&2
    exit 2
  }
  [[ "$PREBUILT_RELEASE_SHA256" =~ ^[0-9a-fA-F]{64}$ ]] || {
    echo "Expected archive SHA-256 must contain exactly 64 hexadecimal characters." >&2
    exit 2
  }
  [[ "$PREBUILT_RELEASE_COMMIT" =~ ^[0-9a-f]{40}$ ]] || {
    echo "Source commit must be a full 40-character lowercase Git SHA." >&2
    exit 2
  }
  [[ "${ENABLE_POSTDEPLOY_SCHEMA_WORK:-0}" == "0" ]] || {
    echo "Exact-artifact release requires ENABLE_POSTDEPLOY_SCHEMA_WORK=0." >&2
    exit 2
  }
  export PREBUILT_RELEASE_ARTIFACT PREBUILT_RELEASE_SHA256
  export DEPLOY_SOURCE_COMMIT="$PREBUILT_RELEASE_COMMIT"
fi

PROJECT_PYTHON="${PYTHON:-$REPO_ROOT/../venv/interoves_django/bin/python}"
SOURCE_DIR="$(mktemp -d /tmp/interoves-deploy-source.XXXXXX)"
trap 'rm -rf "$SOURCE_DIR"' EXIT

if [[ -n "${PREBUILT_RELEASE_ARTIFACT:-}" ]]; then
  [[ -f "$PREBUILT_RELEASE_ARTIFACT" ]] || {
    echo "Prebuilt release artifact not found: $PREBUILT_RELEASE_ARTIFACT" >&2
    exit 2
  }
  "$PROJECT_PYTHON" "$REPO_ROOT/scripts/offline_release_artifact.py" verify \
    "$PREBUILT_RELEASE_ARTIFACT" --source-commit "$DEPLOY_SOURCE_COMMIT" \
    --sha256 "$PREBUILT_RELEASE_SHA256"
  SOURCE_SHA="$(unzip -p "$PREBUILT_RELEASE_ARTIFACT" release-manifest.json | \
    "$PROJECT_PYTHON" -c 'import json,sys; print(json.load(sys.stdin)["source_commit"])')"
  [[ "$SOURCE_SHA" == "$DEPLOY_SOURCE_COMMIT" ]] || {
    echo "Prebuilt artifact source commit does not match the requested commit." >&2
    exit 2
  }
  "$PROJECT_PYTHON" "$REPO_ROOT/scripts/eb_config_manifest.py" verify-zip \
    "$PREBUILT_RELEASE_ARTIFACT" interoves-web-green-lb "$SOURCE_SHA"
  git cat-file -e "$SOURCE_SHA^{commit}" || {
    echo "Prebuilt artifact source commit is not present in this repository." >&2
    exit 2
  }
  members="$(unzip -Z1 "$PREBUILT_RELEASE_ARTIFACT")"
  for required_member in \
    Procfile \
    nutrimatic_bundle/build/find-expr \
    nutrimatic_bundle/cgi_scripts/cgi-search.py \
    interoves_django/deploy_version.txt \
    interoves_django/static_release_id.txt \
    interoves_eb_config_manifest.json; do
    grep -Fxq "$required_member" <<< "$members" || {
      echo "Prebuilt artifact is missing required file: $required_member" >&2
      exit 2
    }
  done
  SOURCE_SHORT_SHA="$(unzip -p "$PREBUILT_RELEASE_ARTIFACT" interoves_django/deploy_version.txt)"
  if [[ "${BUNDLE_MICROSITES:-0}" == "1" ]]; then
    echo "Cannot mutate microsite sources in an immutable prebuilt artifact." >&2
    exit 2
  fi
  echo "Release source: $SOURCE_SHA (verified immutable artifact)."
else
  SOURCE_SHA="$(git rev-parse HEAD)"
  SOURCE_SHORT_SHA="$(git rev-parse --short HEAD)"
  # Never let unrelated or uncommitted checkout files leak into a production zip.
  git archive "$SOURCE_SHA" | tar -x -C "$SOURCE_DIR"
  echo "Release source: $SOURCE_SHA (committed snapshot; working-tree edits excluded)."
fi

# Local-only settings are needed for preflight/collectstatic but are excluded
# from EB bundles by .ebignore. Link only the one required Django secret.
if [[ -z "${PREBUILT_RELEASE_ARTIFACT:-}" && -f "$REPO_ROOT/secrets/django_secret_key.txt" ]]; then
  ln -s "$REPO_ROOT/secrets/django_secret_key.txt" "$SOURCE_DIR/secrets/django_secret_key.txt"
fi

export DEPLOY_SOURCE_DIR="$SOURCE_DIR"
export DEPLOY_SOURCE_SHA="$SOURCE_SHORT_SHA"
export DEPLOY_SOURCE_COMMIT="$SOURCE_SHA"
export PYTHON="$PROJECT_PYTHON"

if [[ -z "${PREBUILT_RELEASE_ARTIFACT:-}" && "${BUNDLE_MICROSITES:-0}" == "1" ]]; then
  "$SOURCE_DIR/scripts/bundle_microsites.sh"
else
  echo "Skipping microsite bundling (set BUNDLE_MICROSITES=1 to refresh local bundles)."
fi
if [[ -z "$PREBUILT_RELEASE_ARTIFACT" ]]; then
  if [[ ! -x "$SOURCE_DIR/nutrimatic_bundle/build/find-expr" ]]; then
    echo "ERROR: nutrimatic_bundle/build/find-expr missing or not executable." >&2
    echo "Run scripts/bundle_microsites.sh (needs ~/nutrimatic-ru/build/find-expr) or restore the committed binary." >&2
    exit 1
  fi
  if [[ ! -f "$SOURCE_DIR/nutrimatic_bundle/cgi_scripts/cgi-search.py" ]]; then
    echo "ERROR: nutrimatic_bundle/cgi_scripts/cgi-search.py missing." >&2
    exit 1
  fi
fi
"$REPO_ROOT/scripts/deploy_green.sh" "$DEPLOY_MODE"
