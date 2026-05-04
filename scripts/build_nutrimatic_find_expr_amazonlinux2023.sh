#!/usr/bin/env bash
# Build nutrimatic-ru build/find-expr inside Amazon Linux 2023 (same family as EB
# "Python 3.12 running on 64bit Amazon Linux 2023") so the binary does not require
# glibc newer than on the instance (e.g. GLIBC_2.38 from Ubuntu 24.04 builds).
#
# Prerequisites: Docker, nutrimatic-ru source with Conan/meson layout (see that repo README).
# Alternative without Docker: build on a real Amazon Linux 2023 host (e.g. small EC2) using
# scripts/build_nutrimatic_find_expr_amazonlinux2023_native.sh
#
# Usage:
#   ./scripts/build_nutrimatic_find_expr_amazonlinux2023.sh
#   NUTRIMATIC_SRC=/path/to/nutrimatic-ru ./scripts/build_nutrimatic_find_expr_amazonlinux2023.sh
#
# Output: $NUTRIMATIC_SRC/build/find-expr (overwrites). Then run ./scripts/bundle_microsites.sh && ./deploy.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${NUTRIMATIC_SRC:-$HOME/nutrimatic-ru}"

if [[ ! -f "$SRC/conanfile.py" ]]; then
  echo "Nutrimatic source not found (expected conanfile.py): $SRC" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found; install Docker or build on an Amazon Linux 2023 host." >&2
  exit 1
fi

mkdir -p "$SRC/build"

echo "Building find-expr in amazonlinux:2023 from $SRC ..."

docker run --rm -i \
  --platform linux/amd64 \
  -e CONAN_HOME=/conan-cache \
  -v "${SRC}:/src:ro" \
  -v "${SRC}/build:/out-build" \
  -v nutrimatic-al2023-conan-cache:/conan-cache \
  amazonlinux:2023 \
  bash -s << 'EOS'
set -euo pipefail
dnf install -y \
  gcc gcc-c++ \
  python3 python3-pip \
  cmake ninja-build \
  pkgconf-pkg-config git \
  rsync \
  binutils \
  >/dev/null
pip3 install --no-cache-dir "conan>=2" meson

mkdir -p /work
rsync -a --delete --exclude build --exclude .git /src/ /work/
cd /work

conan profile detect --force
PROFILE_PATH=$(conan profile path default)
if ! grep -q '^compiler\.cppstd=' "$PROFILE_PATH" 2>/dev/null; then
  echo "compiler.cppstd=17" >> "$PROFILE_PATH"
fi

conan install . --build=missing
conan build .

install -D -m0755 build/find-expr /out-build/find-expr
echo "--- GLIBC requirements (Amazon Linux 2023 ships glibc 2.34; symbols must stay compatible): ---"
if command -v objdump >/dev/null 2>&1; then
  objdump -T /out-build/find-expr | grep -F GLIBC_ | sed 's/.*\(GLIBC_[0-9.]*\).*/\1/' | sort -u | tail -15
  # Fail if binary needs GLIBC newer than 2.34 (e.g. 2.38 from Ubuntu 24.04 toolchains).
  if objdump -T /out-build/find-expr | grep -qE 'GLIBC_2\.(3[5-9]|[4-9][0-9])\b'; then
    echo "error: find-expr requires GLIBC newer than AL2023 provides; rebuild only inside this container." >&2
    exit 1
  fi
else
  ldd /out-build/find-expr
fi
EOS

echo "OK: $SRC/build/find-expr"
echo "Next: ./scripts/bundle_microsites.sh && ./deploy.sh"
