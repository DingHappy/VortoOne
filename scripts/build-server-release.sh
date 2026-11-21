#!/bin/sh
# 做常开电脑用的发布文件（deploy/install.sh 和 Docker 镜像用的就是这些）：
#   VERSION、vortocode-<版本>-py3-none-any.whl、vortoone-web-<版本>.tar.gz、constraints.txt、install.sh、SHA256SUMS
#
#   scripts/build-server-release.sh <输出目录>
#
# 界面要先构建好（frontend：npm ci && npm run build --workspace @vortoone/web）。版本取 pyproject.toml。
# 只在本地生成文件，不上传；发布由 .github/workflows/server-release.yml 在推送 server-v<版本> 标签时做。
set -eu
OUT=${1:?要给输出目录}
ROOT=$(cd "$(dirname "$0")/.." && pwd)
VERSION=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -1)
[ -n "$VERSION" ] || { echo "pyproject.toml 里读不到版本" >&2; exit 1; }
WEB="$ROOT/frontend/apps/web/dist"
[ -f "$WEB/index.html" ] || { echo "界面还没构建：$WEB/index.html 不存在" >&2; exit 1; }

mkdir -p "$OUT"
rm -f "$OUT"/vortocode-*.whl "$OUT"/vortoone-web-*.tar.gz
uv build --wheel -q -o "$OUT" "$ROOT"
[ -f "$OUT/vortocode-$VERSION-py3-none-any.whl" ] || { echo "没生成 vortocode-$VERSION 的安装包" >&2; exit 1; }
tar -czf "$OUT/vortoone-web-$VERSION.tar.gz" -C "$WEB" .
cp "$ROOT/constraints.txt" "$OUT/constraints.txt"
cp "$ROOT/deploy/install.sh" "$OUT/install.sh"
printf '%s\n' "$VERSION" > "$OUT/VERSION"
cd "$OUT"
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "vortocode-$VERSION-py3-none-any.whl" "vortoone-web-$VERSION.tar.gz" constraints.txt install.sh VERSION > SHA256SUMS
else
  shasum -a 256 "vortocode-$VERSION-py3-none-any.whl" "vortoone-web-$VERSION.tar.gz" constraints.txt install.sh VERSION > SHA256SUMS
fi
echo "${OUT}：VortoOne $VERSION"
