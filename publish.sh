#!/usr/bin/env bash
set -euo pipefail

# Ensure gpg-agent SSH socket is available (works even when SSH_AUTH_SOCK isn't inherited)
export SSH_AUTH_SOCK="${SSH_AUTH_SOCK:-/run/user/$(id -u)/gnupg/S.gpg-agent.ssh}"

VERSION="${1:?Usage: ./publish.sh <version>  e.g. ./publish.sh 1.4.0}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

echo "==> Releasing v$VERSION"

# 1. Bump version in pyproject.toml (skip if already at this version)
CURRENT=$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/')
if [ "$CURRENT" != "$VERSION" ]; then
    sed -i "s/^version = .*/version = \"$VERSION\"/" pyproject.toml
    git add pyproject.toml
    git commit -m "chore: release v$VERSION"
else
    echo "  pyproject.toml already at v$VERSION, skipping commit"
fi

# 2. Tag and push (skip if tag already exists)
if git tag -l "v$VERSION" | grep -q "v$VERSION"; then
    echo "  Tag v$VERSION already exists, skipping"
else
    git tag "v$VERSION"
fi
git push
git push origin "v$VERSION" 2>/dev/null || echo "  Tag already on remote, skipping"

# 3. Build and upload to PyPI (--skip-existing handles already-uploaded versions)
rm -rf dist build ./*.egg-info
python -m build
twine upload --skip-existing dist/*

# 4. Get sha256 of the GitHub tarball
echo "==> Fetching tarball sha256..."
sleep 3
SHA=$(curl -sL "https://github.com/El-Mundos/paraninfodl/archive/v${VERSION}.tar.gz" | sha256sum | awk '{print $1}')
echo "    sha256: $SHA"

# 5. Update PKGBUILD (skip commit if already at this version)
sed -i "s/^pkgver=.*/pkgver=$VERSION/" PKGBUILD
sed -i "s/^pkgrel=.*/pkgrel=1/" PKGBUILD
sed -i "s/^sha256sums=.*/sha256sums=('$SHA')/" PKGBUILD
makepkg --printsrcinfo > .SRCINFO
git add PKGBUILD .SRCINFO
if git diff --cached --quiet; then
    echo "  PKGBUILD already up to date, skipping commit"
else
    git commit -m "chore: update PKGBUILD to v$VERSION"
fi
git push

# 6. Push to AUR
TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT
git clone ssh://aur@aur.archlinux.org/paraninfodl.git "$TMPDIR"
cp PKGBUILD .SRCINFO "$TMPDIR/"
cd "$TMPDIR"
git add PKGBUILD .SRCINFO
if git diff --cached --quiet; then
    echo "  AUR already up to date"
else
    git commit -m "update to v$VERSION"
    git push origin HEAD:master
fi

echo ""
echo "✓ v$VERSION released"
echo "  PyPI  : https://pypi.org/project/paraninfodl/$VERSION/"
echo "  AUR   : https://aur.archlinux.org/packages/paraninfodl"
echo "  GitHub: https://github.com/El-Mundos/paraninfodl/releases/tag/v$VERSION"
