#!/usr/bin/env bash
# Build nutrimatic find-expr on Amazon Linux 2023 (native — no Docker).
# The binary must be linked against the same glibc family as Elastic Beanstalk
# ("Python 3.12 running on 64bit Amazon Linux 2023"). Building on Ubuntu/WSL
# will keep producing GLIBC_2.38 errors on EB.
#
# Typical flow:
#   1. Launch a small EC2 (Amazon Linux 2023, x86_64), attach SG with SSH from your IP.
#   2. scp -r ~/nutrimatic-ru ec2-user@INSTANCE:~/nutrimatic-ru
#      (or: git clone your fork on the instance)
#   3. On the instance:
#        chmod +x build_nutrimatic_find_expr_amazonlinux2023_native.sh
#        ./build_nutrimatic_find_expr_amazonlinux2023_native.sh ~/nutrimatic-ru
#   4. Copy binary back:
#        scp ec2-user@INSTANCE:~/nutrimatic-ru/build/find-expr \\
#            ~/nutrimatic-ru/build/find-expr
# On the EB instance (same glibc as prod):
#   eb ssh
#   From laptop: scp -i ~/.ssh/key -r ~/nutrimatic-ru ec2-user@<instance-ip>:~/
#   Copy this script to the instance (scp) or nano/paste it.
#   INSTALL_NUTRIMATIC_FIND_EXPR_TO_EB=1 ./build_nutrimatic_find_expr_amazonlinux2023_native.sh ~/nutrimatic-ru
#   Installs to /var/app/current/nutrimatic_bundle/build/find-expr. Each eb deploy overwrites it —
#   scp the binary back to ~/nutrimatic-ru/build/ locally and commit/bundle if you want it permanent.
#
# Usage on AL2023:  ./scripts/build_nutrimatic_find_expr_amazonlinux2023_native.sh [NUTRIMATIC_SRC]

set -euo pipefail

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot read /etc/os-release — are you on Linux?" >&2
  exit 1
fi
# shellcheck source=/dev/null
source /etc/os-release
ON_EB=false
[[ -d /var/app/current ]] && ON_EB=true

if ! $ON_EB; then
  if [[ "${ID:-}" != "amzn" ]] || [[ "${VERSION_ID:-}" != "2023" ]]; then
    echo "Run this script only on plain Amazon Linux 2023 (e.g. EC2 amzn2023 AMI)." >&2
    echo "Current: ID=$ID VERSION_ID=$VERSION_ID" >&2
    echo "On EB: ssh in (eb ssh), put nutrimatic-ru on the box, set INSTALL_NUTRIMATIC_FIND_EXPR_TO_EB=1" >&2
    echo "On your laptop use: Docker script scripts/build_nutrimatic_find_expr_amazonlinux2023.sh" >&2
    exit 1
  fi
elif [[ "${ID:-}" != "amzn" ]] || [[ "${VERSION_ID:-}" != "2023" ]]; then
  echo "Warning: /var/app/current exists but OS is not amzn 2023 ($ID $VERSION_ID); continuing." >&2
fi

SRC="${1:-$HOME/nutrimatic-ru}"
if [[ ! -f "$SRC/conanfile.py" ]]; then
  echo "Nutrimatic tree not found (no conanfile.py): $SRC" >&2
  exit 1
fi

if [[ "$(uname -m)" != "x86_64" ]]; then
  echo "Expected x86_64 (EB instances). Got: $(uname -m)" >&2
  exit 1
fi

echo "Installing build dependencies (dnf)..."
sudo dnf install -y \
  gcc gcc-c++ \
  python3 python3-pip \
  cmake ninja-build \
  pkgconf-pkg-config git \
  rsync \
  binutils

echo "Installing Conan + Meson..."
pip3 install --user "conan>=2" meson
export PATH="${HOME}/.local/bin:${PATH}"

cd "$SRC"
conan profile detect --force
PROFILE_PATH="$(conan profile path default)"
if ! grep -q '^compiler\.cppstd=' "$PROFILE_PATH" 2>/dev/null; then
  echo "compiler.cppstd=17" >> "$PROFILE_PATH"
fi

echo "Conan install (may take a long time the first time)..."
conan install . --build=missing

echo "Conan build..."
conan build .

if [[ ! -f build/find-expr ]]; then
  echo "Expected $SRC/build/find-expr after build — not found." >&2
  exit 1
fi

chmod +x build/find-expr
echo "OK: $SRC/build/find-expr"
echo "--- GLIBC symbols (top few): ---"
if command -v objdump >/dev/null 2>&1; then
  objdump -T build/find-expr | grep -F GLIBC_ | sed 's/.*\(GLIBC_[0-9.]*\).*/\1/' | sort -u | tail -15
fi

if [[ "${INSTALL_NUTRIMATIC_FIND_EXPR_TO_EB:-}" == 1 ]]; then
  EB_BIN=/var/app/current/nutrimatic_bundle/build/find-expr
  if [[ ! -d "$(dirname "$EB_BIN")" ]]; then
    echo "Cannot install: $(dirname "$EB_BIN") missing (deploy nutrimatic_bundle first)." >&2
    exit 1
  fi
  echo "Installing into $EB_BIN (needs sudo)..."
  sudo install -m0755 build/find-expr "$EB_BIN"
  echo "Live app now uses new find-expr; reload not required for Nutrimatic (subprocess per request)."
  echo "IMPORTANT: the next eb deploy will replace this file — save a copy to your repo:"
  echo "  scp ec2-user@HOST:$EB_BIN ~/nutrimatic-ru/build/find-expr"
else
  echo "Copy this binary to your dev machine nutrimatic-ru/build/, then bundle_microsites.sh + deploy."
  echo "Or on EB re-run with: INSTALL_NUTRIMATIC_FIND_EXPR_TO_EB=1"
fi
