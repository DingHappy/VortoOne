# syntax=docker/dockerfile:1.7
# VortoOne 的 Docker 镜像：和 deploy/install.sh 装的是同一份发布文件（scripts/build-server-release.sh 生成）。
#
#   scripts/build-server-release.sh dist/release
#   docker build -f deploy/Dockerfile -t vortoone dist/release
#
# 数据都在 /data（挂一个卷）：home/ 是家，server.env 是设置（第一次启动时生成所有者令牌）。
# 容器里监听 0.0.0.0:8080，但所有接口都要所有者令牌或登录；端口只映射到宿主机的 127.0.0.1（见 docker-compose.yml），
# 别的设备经 Tailscale 连进来。还没有所有者时，启动日志里打印一次性的认领链接。
FROM python:3.12-slim-bookworm

ARG VERSION
RUN test -n "$VERSION" \
 && useradd --uid 10001 --create-home --home-dir /home/vortoone vortoone \
 && mkdir -p /data /opt/vortoone && chown vortoone:vortoone /data

COPY --chmod=0644 vortocode-${VERSION}-py3-none-any.whl constraints.txt vortoone-web-${VERSION}.tar.gz SHA256SUMS /tmp/release/
RUN cd /tmp/release \
 && grep -E " (vortocode-${VERSION}-py3-none-any.whl|constraints.txt|vortoone-web-${VERSION}.tar.gz)$" SHA256SUMS | sha256sum -c - \
 && python -m venv /opt/vortoone/venv \
 && /opt/vortoone/venv/bin/pip install --no-cache-dir --constraint constraints.txt "vortocode-${VERSION}-py3-none-any.whl[llm,mcp]" \
 && mkdir -p /opt/vortoone/web && tar -xzf vortoone-web-${VERSION}.tar.gz -C /opt/vortoone/web \
 && test -f /opt/vortoone/web/index.html \
 && rm -rf /tmp/release

COPY --chmod=0755 <<'EOF' /usr/local/bin/vortoone-start
#!/bin/sh
# 第一次启动生成所有者令牌；还没有所有者时打印认领链接；然后启动服务。
set -eu
umask 077
mkdir -p /data/home
if [ ! -f /data/server.env ]; then
  {
    echo "# VortoOne 服务的设置。所有者令牌只在这里：别给别人看。改完重启容器生效。"
    echo "VORTOONE_API_TOKEN=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')"
    echo "# 别的设备经 Tailscale 连进来时，填它在 tailnet 里的地址，例如 https://vortoone.tail1234.ts.net"
    echo "VORTOONE_PUBLIC_URL="
    echo "OPENAI_BASE_URL="
    echo "OPENAI_API_KEY="
  } > /data/server.env
fi
set -a
. /data/server.env
set +a
[ -n "${VORTOONE_PUBLIC_URL:-}" ] || unset VORTOONE_PUBLIC_URL
export VORTOONE_APP_UI_DIR=/opt/vortoone/web
cd /data/home
link=$(/opt/vortoone/venv/bin/vc owner-claim --url "${VORTOONE_PUBLIC_URL:-http://127.0.0.1:8080}" 2>/dev/null \
       | sed -n 's/.*"link": *"\([^"]*\)".*/\1/p' || true)
if [ -n "$link" ]; then
  echo "所有者认领链接（24 小时内有效，只能用一次；第一个打开的人成为所有者）："
  echo "    $link"
fi
exec /opt/vortoone/venv/bin/vc server --host 0.0.0.0 --port 8080
EOF

USER vortoone
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health/quick', timeout=4)"
ENTRYPOINT ["/usr/local/bin/vortoone-start"]
