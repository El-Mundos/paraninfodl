#!/usr/bin/env bash
set -euo pipefail

# Ensure gpg-agent SSH socket is available (works even when SSH_AUTH_SOCK isn't inherited)
export SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-/run/user/$(id -u)/gnupg/S.gpg-agent.ssh}"

VERSION="${1:?Usage: ./publish.sh <version>  e.g. ./publish.sh 1.1.0}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Releasing v$VERSION"

# 1. Bump version in pyproject.toml
sed -i "s/^version = .*/version = \"$VERSION\"/" pyproject.toml

# 2. Commit, tag, push — tag must exist before we can fetch the tarball sha256
git add pyproject.toml
git commit -m "chore: release v$VERSION"
git tag "v$VERSION"
git push
git push origin "v$VERSION"

# 3. Build and upload to PyPI
rm -rf dist build ./*.egg-info
python -m build
twine upload dist/*

# 4. Get sha256 of the GitHub tarball (GitHub needs a moment after tag push)
echo "==> Fetching tarball sha256..."
sleep 3
SHA=$(curl -sL "https://github.com/El-Mundos/paraninfodl/archive/v${VERSION}.tar.gz" | sha256sum | awk '{print $1}')
echo "    sha256: $SHA"

# 5. Update PKGBUILD
sed -i "s/^pkgver=.*/pkgver=$VERSION/" PKGBUILD
sed -i "s/^pkgrel=.*/pkgrel=1/" PKGBUILD
sed -i "s/^sha256sums=.*/sha256sums=('$SHA')/" PKGBUILD

# 6. Regenerate .SRCINFO
makepkg --printsrcinfo > .SRCINFO

# 7. Commit and push PKGBUILD + .SRCINFO to GitHub
git add PKGBUILD .SRCINFO
git commit -m "chore: update PKGBUILD to v$VERSION"
git push

# 8. Push to AUR
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
git clone ssh://aur@aur.archlinux.org/paraninfodl.git "$TMPDIR"
cp PKGBUILD .SRCINFO "$TMPDIR/"
cd "$TMPDIR"
git add PKGBUILD .SRCINFO
git commit -m "update to v$VERSION"
git push origin HEAD:master

echo ""
echo "✓ v$VERSION released"
echo "  PyPI : https://pypi.org/project/paraninfodl/$VERSION/"
echo "  AUR  : https://aur.archlinux.org/packages/paraninfodl"
echo "  GitHub: https://github.com/El-Mundos/paraninfodl/releases/tag/v$VERSION"
