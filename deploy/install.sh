#!/bin/sh
# VortoOne 安装命令：把 VortoOne 装在一台常开的电脑上（Linux 或 macOS），一条命令。
#
#   curl -fsSL https://github.com/DingHappy/VortoOne/releases/download/server-latest/install.sh | sh
#   curl -fsSL https://github.com/DingHappy/VortoOne/releases/download/server-latest/install.sh | sh -s -- --plan
#
# 想先看一遍再跑：curl -fsSLo install.sh <上面的地址>，读完再 sh install.sh。
#
# 它做的事（--plan 只打印，不做）：
#   1. 没有 uv 就下载一份固定版本的 uv（核对写死在这里的 SHA-256）。
#   2. 从 GitHub Releases 下载这一版的安装包、界面和依赖锁，按同一版的 SHA256SUMS 核对。
#   3. 装进 <目录>/versions/<版本>/，<目录>/current 指向它（升级时换指向，旧版本留着可以退回）。
#   4. 第一次装时生成所有者令牌（只存在 <目录>/server.env，0600），服务只听 127.0.0.1。
#   5. 登记成开机自启的用户服务（Linux：systemd --user；macOS：launchd），启动，等它应答。
#   6. 打印一次性的所有者认领链接：第一个打开它的人成为所有者。它只出现在这个终端里。
#
# 不做的事：不用 sudo，不改防火墙，不对外开端口。别的设备要连进来，按最后打印的说明用 Tailscale。
#
# 选项：
#   --version <版本>   装哪一版（默认最新一版）
#   --dir <目录>       装到哪里（默认 ~/.local/share/vortoone）
#   --port <端口>      本机端口（默认 8080）
#   --from <目录>      从本地目录取安装文件（离线安装、测试），不下载
#   --no-service       不登记、不启动服务，只装好（之后自己跑 <目录>/run.sh）
#   --plan             只打印要做什么
#
# 整个脚本在 main 里，最后一行才调用：下载到一半断了不会执行半截。
main() {
  set -eu
  REPO=DingHappy/VortoOne
  UV_VERSION=0.12.9
  PYTHON_VERSION=3.12
  EXTRAS=llm,mcp
  VERSION='' DIR="${HOME}/.local/share/vortoone" PORT=8080 FROM='' SERVICE=1 PLAN=0
  while [ $# -gt 0 ]; do
    case $1 in
      --version) VERSION=${2:?--version 后面要跟版本}; shift ;;
      --version=*) VERSION=${1#--version=} ;;
      --dir) DIR=${2:?--dir 后面要跟目录}; shift ;;
      --dir=*) DIR=${1#--dir=} ;;
      --port) PORT=${2:?--port 后面要跟端口}; shift ;;
      --port=*) PORT=${1#--port=} ;;
      --from) FROM=${2:?--from 后面要跟目录}; shift ;;
      --from=*) FROM=${1#--from=} ;;
      --no-service) SERVICE=0 ;;
      --plan | --dry-run) PLAN=1 ;;
      -h | --help) sed -n '2,27p' "$0" 2>/dev/null || true; return 0 ;;
      *) die "不认识的选项：$1（--help 看说明）" ;;
    esac
    shift
  done
  case $PORT in ''|*[!0-9]*) die "端口要是数字：$PORT" ;; esac
  [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "端口要在 1024～65535 之间：$PORT"
  [ "$(id -u)" != 0 ] || die "不要用 root 跑：VortoOne 装在你自己的用户下面，不需要 sudo"

  OS=$(uname -s) ARCH=$(uname -m)
  case $OS-$ARCH in
    Darwin-arm64) UV_TARGET=aarch64-apple-darwin UV_SHA=301f72afaf54060f92da7016cb0115bd077f43a9c8e39c1d8170a0bac80fd398 ;;
    Darwin-x86_64) UV_TARGET=x86_64-apple-darwin UV_SHA=e1ca175824f1056589ce9908f7631879ebc3c36535b5e63dc06510beb370b4c1 ;;
    Linux-x86_64) UV_TARGET=x86_64-unknown-linux-gnu UV_SHA=ec7a99cd05e0cd7f80243f135ce1361c76835cb0ee60055d14d20eba8eba1460 ;;
    Linux-aarch64 | Linux-arm64) UV_TARGET=aarch64-unknown-linux-gnu UV_SHA=c36fe17937ff6bd16dc42fc13854b5465999fcab2efe0af559381e945e3c6001 ;;
    *) die "这台电脑（$OS ${ARCH}）还不支持；可以用 Docker 镜像，见 deploy/docker-compose.yml" ;;
  esac

  if [ -z "$VERSION" ]; then
    if [ -n "$FROM" ]; then
      VERSION=$(cat "$FROM/VERSION" 2>/dev/null) || die "$FROM 里没有 VERSION 文件"
    else
      VERSION=$(fetch_text "https://github.com/$REPO/releases/download/server-latest/VERSION") || die "查不到最新版本（连得上 github.com 吗？）"
    fi
  fi
  VERSION=$(printf '%s' "$VERSION" | tr -d ' \r\n')
  case $VERSION in ''|*[!0-9A-Za-z.+-]*) die "版本号不对：$VERSION" ;; esac
  BASE="https://github.com/$REPO/releases/download/server-v$VERSION"
  TARGET="$DIR/versions/$VERSION"

  say "VortoOne $VERSION → ${DIR}（端口 ${PORT}，只听 127.0.0.1）"
  if [ "$PLAN" = 1 ]; then
    command -v uv >/dev/null 2>&1 || say "· 下载 uv ${UV_VERSION}（${UV_TARGET}），核对 SHA-256"
    say "· 下载 ${FROM:-$BASE} 的安装包、界面、依赖锁，按 SHA256SUMS 核对"
    say "· 用 Python $PYTHON_VERSION 装进 ${TARGET}，$DIR/current 指向它"
    [ -f "$DIR/server.env" ] || say "· 生成所有者令牌，写进 $DIR/server.env（0600）"
    [ "$SERVICE" = 1 ] && say "· 登记并启动用户服务（$(service_kind)）"
    say "· 打印一次性的所有者认领链接"
    return 0
  fi

  umask 077
  mkdir -p "$DIR/versions" "$DIR/home" "$DIR/downloads"
  UV=$(find_uv)

  # ── 安装文件 ──
  WORK="$DIR/downloads/$VERSION"
  rm -rf "$WORK" && mkdir -p "$WORK"
  WHEEL="vortocode-$VERSION-py3-none-any.whl"
  for name in SHA256SUMS "$WHEEL" "vortoone-web-$VERSION.tar.gz" constraints.txt; do
    if [ -n "$FROM" ]; then cp "$FROM/$name" "$WORK/$name" || die "$FROM 里没有 $name"
    else fetch "$BASE/$name" "$WORK/$name" || die "下载 $name 失败"; fi
  done
  for name in "$WHEEL" "vortoone-web-$VERSION.tar.gz" constraints.txt; do
    want=$(awk -v n="$name" '$2 == n || $2 == "*"n {print $1}' "$WORK/SHA256SUMS")
    [ -n "$want" ] || die "SHA256SUMS 里没有 $name"
    [ "$(sha256 "$WORK/$name")" = "$want" ] || die "$name 的 SHA-256 对不上，停下（文件可能被换过）"
  done
  say "✓ 安装文件已核对"

  # ── 装到 versions/<版本> ──
  # 直接装在最终位置（venv 里的脚本写死了自己的路径，装完不能再改名）；装完才写 .complete，没有它的当没装好、重装
  if [ ! -f "$TARGET/.complete" ]; then
    rm -rf "$TARGET"
    "$UV" venv -q --python "$PYTHON_VERSION" "$TARGET"
    "$UV" pip install -q --python "$TARGET/bin/python" --constraint "$WORK/constraints.txt" "$WORK/$WHEEL[$EXTRAS]"
    mkdir -p "$TARGET/web"
    tar -xzf "$WORK/vortoone-web-$VERSION.tar.gz" -C "$TARGET/web"
    [ -f "$TARGET/web/index.html" ] || die "界面包里没有 index.html"
    "$TARGET/bin/vc" --help >/dev/null 2>&1 || die "装好的 vc 跑不起来"
    printf '%s\n' "$VERSION" > "$TARGET/.complete"
  fi
  ln -sfn "versions/$VERSION" "$DIR/current"
  say "✓ 已装好 $VERSION"

  # ── 配置与启动脚本 ──
  if [ ! -f "$DIR/server.env" ]; then
    {
      echo "# VortoOne 服务的设置。所有者令牌只在这里：别给别人看。改完重启服务生效。"
      echo "VORTOONE_API_TOKEN=$(random_hex)"
      echo "VORTOONE_PORT=$PORT"
      echo "# 别的设备经 Tailscale 连进来时，填它在 tailnet 里的地址，例如 https://vortoone.tail1234.ts.net"
      echo "VORTOONE_PUBLIC_URL="
      echo "# 模型：OpenAI 兼容的地址和密钥（也可以以后在界面里填）"
      echo "OPENAI_BASE_URL="
      echo "OPENAI_API_KEY="
    } > "$DIR/server.env"
    chmod 600 "$DIR/server.env"
  fi
  cat > "$DIR/run.sh" <<EOF
#!/bin/sh
# 启动 VortoOne 服务（用户服务调用它；也可以手动跑）。设置在 server.env。
set -eu
cd "$DIR/home"
set -a
. "$DIR/server.env"
set +a
[ -n "\${VORTOONE_PUBLIC_URL:-}" ] || unset VORTOONE_PUBLIC_URL
export VORTOONE_APP_UI_DIR="$DIR/current/web"
exec "$DIR/current/bin/vc" server --host 127.0.0.1 --port "\${VORTOONE_PORT:-$PORT}"
EOF
  chmod 700 "$DIR/run.sh"

  if [ "$SERVICE" = 1 ]; then
    install_service
    wait_ready || die "服务没有在 60 秒内应答；看 $DIR/server.log"
    say "✓ 服务已启动：http://127.0.0.1:$PORT"
  fi

  # ── 所有者认领 ──
  URL=$(sed -n 's/^VORTOONE_PUBLIC_URL=//p' "$DIR/server.env" | tail -1)
  [ -n "$URL" ] || URL="http://127.0.0.1:$PORT"
  CLAIM=$(cd "$DIR/home" && "$DIR/current/bin/vc" owner-claim --url "$URL" 2>/dev/null || true)
  LINK=$(printf '%s' "$CLAIM" | sed -n 's/.*"link": *"\([^"]*\)".*/\1/p')
  say ""
  if [ -n "$LINK" ]; then
    say "所有者认领链接（24 小时内有效，只能用一次；第一个打开的人成为所有者）："
    say ""
    say "    $LINK"
    say ""
    say "在这台电脑的浏览器里打开它，填名字、邮箱和密码。链接别发给别人。"
  else
    say "这个家已经有所有者了。忘了密码：cd $DIR/home && $DIR/current/bin/vc owner-claim --reset"
  fi
  say ""
  say "让手机和别的电脑连进来（推荐 Tailscale，只在你自己的设备之间）："
  say "  1. 装好 Tailscale 并登录，把这台电脑在 Tailscale 里改名为 vortoone（Desktop 能自己找到它）"
  say "  2. tailscale serve --bg http://127.0.0.1:$PORT"
  say "  3. 在 $DIR/server.env 里填 VORTOONE_PUBLIC_URL=https://vortoone.<你的 tailnet>.ts.net，重启服务"
  [ "$SERVICE" = 1 ] || say "没有登记服务：自己跑 $DIR/run.sh 启动。"
}

say() { printf '%s\n' "$*"; }
die() { printf 'VortoOne 安装：%s\n' "$*" >&2; exit 1; }

fetch() { curl -fsSL --proto '=https' --tlsv1.2 --retry 2 -o "$2" "$1"; }
fetch_text() { curl -fsSL --proto '=https' --tlsv1.2 --retry 2 "$1"; }

sha256() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}

random_hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }

service_kind() { [ "$(uname -s)" = Darwin ] && echo "launchd" || echo "systemd --user"; }

# 有 uv 就用；没有就把固定版本下载到 <目录>/tools，核对 SHA-256 后再用
find_uv() {
  if command -v uv >/dev/null 2>&1; then command -v uv; return; fi
  tools="$DIR/tools/uv-$UV_VERSION"
  if [ ! -x "$tools/uv" ]; then
    tmp="$DIR/downloads/uv-$UV_VERSION.tar.gz"
    fetch "https://github.com/astral-sh/uv/releases/download/$UV_VERSION/uv-$UV_TARGET.tar.gz" "$tmp" \
      || die "下载 uv 失败"
    [ "$(sha256 "$tmp")" = "$UV_SHA" ] || die "uv 的 SHA-256 对不上，停下"
    mkdir -p "$tools"
    tar -xzf "$tmp" -C "$tools" --strip-components 1
    rm -f "$tmp"
  fi
  echo "$tools/uv"
}

wait_ready() {
  i=0
  while [ $i -lt 120 ]; do
    curl -fsS "http://127.0.0.1:$PORT/api/health/quick" >/dev/null 2>&1 && return 0
    sleep 0.5; i=$((i + 1))
  done
  return 1
}

install_service() {
  if [ "$(uname -s)" = Darwin ]; then
    plist="$HOME/Library/LaunchAgents/com.vorto.vortoone.server.plist"
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.vorto.vortoone.server</string>
  <key>ProgramArguments</key><array><string>$DIR/run.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$DIR/server.log</string>
  <key>StandardErrorPath</key><string>$DIR/server.log</string>
</dict>
</plist>
EOF
    launchctl bootout "gui/$(id -u)/com.vorto.vortoone.server" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$(id -u)" "$plist"
  else
    command -v systemctl >/dev/null 2>&1 || die "没有 systemd：加 --no-service，自己用 $DIR/run.sh 启动"
    unit="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/vortoone.service"
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<EOF
[Unit]
Description=VortoOne server
After=network-online.target

[Service]
ExecStart=$DIR/run.sh
Restart=on-failure
RestartSec=5
UMask=0077
StandardOutput=append:$DIR/server.log
StandardError=append:$DIR/server.log

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now vortoone.service
    systemctl --user restart vortoone.service
    if command -v loginctl >/dev/null 2>&1 && [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != yes ]; then
      say "提示：要让它在你没登录时也运行，跑一次 sudo loginctl enable-linger $(id -un)"
    fi
  fi
}

main "$@"
