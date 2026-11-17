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
#   5. 登记成开机自启的用户服务（Linux：systemd --user；macOS：launchd），启动，等它应答；
#      另登记每天 3:30 的备份（<目录>/backups，只留最新 7 份）。
#   6. 打印一次性的所有者认领链接：第一个打开它的人成为所有者。它只出现在这个终端里。
#
# 升级就是再跑一次（或加 --version）：先停服务、备份，换到新版本启动，限定时间里自检（应答、报的是新版本）。
# 自检不过就退回旧版本和升级前的数据，原因写进 <目录>/upgrade.json。旧版本留一个，更早的删掉。
#
# 不做的事：不用 sudo，不改防火墙，不对外开端口。别的设备要连进来，按最后打印的说明用 Tailscale。
#
# 选项：
#   --version <版本>   装哪一版（默认最新一版）
#   --dir <目录>       装到哪里（默认 ~/.local/share/vortoone）
#   --port <端口>      本机端口（默认 8080；已经装过的以 server.env 里的为准）
#   --from <目录>      从本地目录取安装文件（离线安装、测试），不下载
#   --no-service       不登记、不启动服务，只装好（之后自己跑 <目录>/run.sh）
#   --background       不登记服务，在后台直接启动（没有 systemd 的机器、测试）；不登记每天的备份
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
      --background) SERVICE=2 ;;
      --plan | --dry-run) PLAN=1 ;;
      -h | --help) sed -n '2,32p' "$0" 2>/dev/null || true; return 0 ;;
      *) die "不认识的选项：${1}（--help 看说明）" ;;
    esac
    shift
  done
  case $PORT in ''|*[!0-9]*) die "端口要是数字：$PORT" ;; esac
  [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ] || die "端口要在 1024～65535 之间：$PORT"
  [ "$(id -u)" != 0 ] || die "不要用 root 跑：VortoOne 装在你自己的用户下面，不需要 sudo"
  case $DIR in /*) ;; *) die "--dir 要是绝对路径：$DIR" ;; esac
  [ "$DIR" != / ] && [ "$DIR" != "$HOME" ] || die "--dir 不能是 / 或你的主目录本身"

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
    [ "$SERVICE" = 1 ] && say "· 登记并启动用户服务（$(service_kind)），登记每天的备份"
    if [ -L "$DIR/current" ] && [ "$(current_version)" != "$VERSION" ]; then
      say "· 从 $(current_version) 升级：停服务、备份、换版本、自检，不过就退回"
    fi
    say "· 打印一次性的所有者认领链接"
    return 0
  fi

  umask 077
  mkdir -p "$DIR/versions" "$DIR/home" "$DIR/downloads"
  UV=$(find_uv)

  # ── 安装文件 ──
  WORK="$DIR/downloads/$VERSION"
  rm -rf -- "${DIR:?}/downloads/${VERSION:?}"
  mkdir -p "$WORK"
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
    rm -rf -- "${DIR:?}/versions/${VERSION:?}"
    "$UV" venv -q --python "$PYTHON_VERSION" "$TARGET"
    "$UV" pip install -q --python "$TARGET/bin/python" --constraint "$WORK/constraints.txt" "$WORK/$WHEEL[$EXTRAS]"
    mkdir -p "$TARGET/web"
    tar -xzf "$WORK/vortoone-web-$VERSION.tar.gz" -C "$TARGET/web"
    [ -f "$TARGET/web/index.html" ] || die "界面包里没有 index.html"
    "$TARGET/bin/vc" --help >/dev/null 2>&1 || die "装好的 vc 跑不起来"
    printf '%s\n' "$VERSION" > "$TARGET/.complete"
  fi
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
  PORT=$(sed -n 's/^VORTOONE_PORT=//p' "$DIR/server.env" | tail -1)
  case $PORT in ''|*[!0-9]*) PORT=8080 ;; esac
  write_scripts

  # ── 换到这一版：第一次装直接用；升级要先备份，自检不过就退回 ──
  PREVIOUS=$(current_version)
  if [ -z "$PREVIOUS" ] || [ "$PREVIOUS" = "$VERSION" ] || [ ! -f "$DIR/versions/$PREVIOUS/.complete" ]; then
    ln -sfn "versions/$VERSION" "$DIR/current"
    if [ "$SERVICE" != 0 ]; then
      start_server || die "服务启动失败：${WHY:-服务管理器没有完成启动}；看 $DIR/server.log"
      self_check || die "${WHY}；看 $DIR/server.log"
      say "✓ 服务已启动：http://127.0.0.1:$PORT"
    fi
  else
    upgrade "$PREVIOUS"
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
  [ "$SERVICE" != 0 ] || say "没有启动：自己跑 $DIR/run.sh。"
  [ "$SERVICE" = 1 ] || say "没有登记每天的备份：可以用 cron 每天跑一次 $DIR/backup.sh。"
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

current_version() { readlink "$DIR/current" 2>/dev/null | sed -n 's|^versions/||p'; }

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

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
    rm -f -- "$tmp"
  fi
  echo "$tools/uv"
}

write_scripts() {
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
export VORTOONE_UPGRADE_FILE="$DIR/upgrade.json"
exec "$DIR/current/bin/vc" server --host 127.0.0.1 --port "\${VORTOONE_PORT:-$PORT}"
EOF
  cat > "$DIR/backup.sh" <<EOF
#!/bin/sh
# 备份这个家到 $DIR/backups，只留最新 7 份（每天 3:30 跑一次；也可以手动跑）。
set -eu
cd "$DIR/home"
exec "$DIR/current/bin/vc" home-backup --dir "$DIR/backups" --keep 7 --reason "\${1:-daily}"
EOF
  chmod 700 "$DIR/run.sh" "$DIR/backup.sh"
}

# 升级和回退都必须先确认进程退出；任何失败都留下阶段，不能把受理启动当成升级成功。
upgrade() {
  from=$1 saved=''
  say "从 $from 升级到 ${VERSION}："
  if [ "$SERVICE" != 0 ] && ! stop_server; then
    record false "$from" "${WHY}" "" stop_old_failed
    die "旧服务没有确认停止，没有切换版本：${WHY}"
  fi
  if backup_result=$(cd "$DIR/home" && "$TARGET/bin/vc" home-backup --dir "$DIR/backups" --keep 7 --reason upgrade 2>/dev/null); then
    saved=$(printf '%s' "$backup_result" | sed -n 's/.*"path": *"\([^"]*\)".*/\1/p')
  fi
  if [ -z "$saved" ]; then
    why="升级前的备份没做成，没有切换版本"
    stage=backup_failed
    if [ "$SERVICE" != 0 ]; then
      if ! start_server || ! self_check "$from"; then
        why="${why}；旧服务未恢复：${WHY}"
        stage=backup_failed_restart_failed
      fi
    fi
    record false "$from" "$why" "" "$stage"
    die "${why}（版本仍是 ${from}）"
  fi
  say "✓ 升级前已备份：$saved"
  if ! ln -sfn "versions/$VERSION" "$DIR/current"; then
    record false "$from" "切换版本指向失败，服务保持停止" "$saved" switch_failed
    die "切换版本指向失败；备份在 ${saved}，请检查 current 后手动启动"
  fi
  if [ "$SERVICE" = 0 ]; then
    record true "$from" "" "$saved" not_started
    say "✓ 已换到 ${VERSION}；没有启动服务，下次启动生效。不行就 ln -sfn versions/$from $DIR/current 退回"
    return 0
  fi
  if ! start_server; then
    why="新版本启动失败：${WHY:-服务管理器没有完成启动}"
    failure_stage=start_failed
  elif self_check; then
    record true "$from" "" "$saved" completed
    # 服务已经起来了，自己读不到这次的结果：直接写进它的通知（和它启动时读的是同一段程序）
    (cd "$DIR/home" && VORTOONE_UPGRADE_FILE="$DIR/upgrade.json" "$TARGET/bin/python" -c \
      'import os; from src.scheduling.upgrade_notice import report; report(os.getcwd())' >/dev/null 2>&1) || true
    prune "$from"
    say "✓ 已升级到 ${VERSION}，服务在 http://127.0.0.1:$PORT"
    return 0
  else
    why=$WHY
    failure_stage=self_check_failed
  fi
  say "✗ 新版本未通过（${why}），准备退回 $from 和升级前的数据"
  if ! stop_server; then
    record false "$from" "${why}；回退中止：${WHY}" "$saved" rollback_stop_failed "$failure_stage"
    die "回退中止：没有确认新服务退出，未恢复数据。${WHY}"
  fi
  if ! ln -sfn "versions/$from" "$DIR/current"; then
    record false "$from" "${why}；回退时切换版本指向失败" "$saved" rollback_switch_failed "$failure_stage"
    die "回退未完成：切换旧版本失败，服务保持停止；备份在 $saved"
  fi
  if ! (cd "$DIR/home" && { "$DIR/versions/$from/bin/vc" home-restore "$saved" >/dev/null 2>&1 \
                            || "$TARGET/bin/vc" home-restore "$saved" >/dev/null 2>&1; }); then
    record false "$from" "${why}；恢复升级前的数据失败，服务保持停止" "$saved" rollback_restore_failed "$failure_stage"
    die "回退未完成：数据恢复失败，服务保持停止；备份在 $saved"
  fi
  # 先记成「已退回」再启动旧版本：它启动时读到这条，告诉所有者；起不来或自检不过再改记
  record false "$from" "$why" "$saved" rolled_back "$failure_stage"
  if ! start_server; then
    record false "$from" "${why}；旧版本启动失败：${WHY}" "$saved" rollback_start_failed "$failure_stage"
    die "旧版本和数据已恢复，但服务启动失败：${WHY}"
  fi
  if ! self_check "$from"; then
    record false "$from" "${why}；旧版本自检失败：${WHY}" "$saved" rollback_check_failed "$failure_stage"
    die "旧版本和数据已恢复，但服务自检未通过：${WHY}"
  fi
  die "升级到 $VERSION 失败，已退回 ${from}。原因：${why}（详见 $DIR/server.log 和 $DIR/upgrade.json）"
}

# 健康与版本查询共用同一个截止时间；每一次 curl 也有限时，接上却不响应的服务不能拖住回退。
probe() {
  probe_left=$(($2 - $(date +%s)))
  [ "$probe_left" -gt 0 ] || return 1
  [ "$probe_left" -le 2 ] || probe_left=2
  curl -fsS --noproxy '*' --connect-timeout "$probe_left" --max-time "$probe_left" "$1"
}

self_check() {
  WHY=""
  expected=${1:-$VERSION}
  check_deadline=$(($(date +%s) + ${READY_TIMEOUT:-60}))
  if ! wait_ready "$check_deadline"; then
    if [ "$SERVICE" = 2 ] && [ ! -f "$DIR/server.pid" ]; then WHY="服务启动后就退出了"
    else WHY="服务没有在 ${READY_TIMEOUT:-60} 秒内应答"; fi
    return 1
  fi
  if ! instance=$(probe "http://127.0.0.1:$PORT/api/instance" "$check_deadline" 2>/dev/null); then
    WHY="版本自检请求失败或超过限定时间"
    return 1
  fi
  reported=$(printf '%s' "$instance" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p')
  [ "$reported" = "$expected" ] || { WHY="服务报的版本是 ${reported:-没有}，不是 $expected"; return 1; }
}

# 用已安装解释器写 JSON，路径和错误消息中的引号、反斜杠也必须保真。
record() {
  "$TARGET/bin/python" - "$DIR/upgrade.json" "$1" "$2" "$VERSION" "$3" "$4" "${5:-completed}" "${6:-}" <<'PY_RECORD'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
path, ok, old, new, reason, backup, stage, failure_stage = sys.argv[1:]
target = Path(path)
temporary = target.with_suffix('.json.part')
with temporary.open('w', encoding='utf-8') as handle:
    os.chmod(temporary, 0o600)
    json.dump({'ok': ok == 'true', 'from': old, 'to': new,
               'at': datetime.now(timezone.utc).isoformat(), 'reason': reason,
               'backup': backup, 'stage': stage, 'failure_stage': failure_stage},
              handle, ensure_ascii=False)
    handle.write('\n')
os.replace(temporary, target)
PY_RECORD
}

# 只留现在的和刚升级前的那个版本
prune() {
  for old in "$DIR"/versions/*; do
    [ -d "$old" ] || continue
    name=${old##*/}
    [ "$name" = "$VERSION" ] || [ "$name" = "$1" ] || rm -rf -- "${DIR:?}/versions/${name:?}"
  done
}

wait_ready() {
  ready_deadline=${1:-$(($(date +%s) + ${READY_TIMEOUT:-60}))}
  while [ "$(date +%s)" -lt "$ready_deadline" ]; do
    if [ "$SERVICE" = 2 ] && [ -f "$DIR/server.pid" ] && ! process_running "$(cat "$DIR/server.pid")"; then
      rm -f -- "$DIR/server.pid"
      return 1
    fi
    probe "http://127.0.0.1:$PORT/api/health/quick" "$ready_deadline" >/dev/null 2>&1 && return 0
    [ "$(date +%s)" -lt "$ready_deadline" ] || break
    sleep 0.2
  done
  return 1
}

start_server() {
  WHY=""
  case $SERVICE in
    2)
      stop_server || return 1
      nohup "$DIR/run.sh" >> "$DIR/server.log" 2>&1 &
      echo $! > "$DIR/server.pid" || { WHY="无法记录后台服务的 PID"; return 1; } ;;
    1) install_service || { WHY="${WHY:-服务管理器没有完成启动}"; return 1; } ;;
  esac
}

process_running() {
  case $1 in ''|*[!0-9]*) return 1 ;; esac
  [ "$1" -gt 1 ] || return 1
  kill -0 "$1" 2>/dev/null || return 1
  # 已退出、等待父进程回收的 zombie 不会继续写数据。
  process_status=$(ps -o stat= -p "$1" 2>/dev/null | tr -d ' ') || return 0
  case $process_status in Z*) return 1 ;; esac
  return 0
}

wait_stopped() {
  stop_deadline=$(($(date +%s) + ${STOP_TIMEOUT:-30}))
  while process_running "$1"; do
    [ "$(date +%s)" -lt "$stop_deadline" ] || { WHY="进程 $1 在 ${STOP_TIMEOUT:-30} 秒内没有退出"; return 1; }
    sleep 0.2
  done
}

bootout_job() {
  launch_domain="gui/$(id -u)"
  launch_job="$launch_domain/$1"
  launchctl print "$launch_domain" >/dev/null 2>&1 || { WHY="无法查询 launchd 用户服务"; return 1; }
  if launch_details=$(launchctl print "$launch_job" 2>/dev/null); then
    launch_pid=$(printf '%s\n' "$launch_details" | sed -n 's/^[[:space:]]*pid = \([0-9]*\).*$/\1/p' | head -1)
    launchctl bootout "$launch_job" >/dev/null 2>&1 || { WHY="launchd 没有停止 $1"; return 1; }
    [ -z "$launch_pid" ] || wait_stopped "$launch_pid" || return 1
  else
    launch_query_status=$?
    # launchctl 对“服务未注册”返回 113；其他错误不能当作已经停止。
    [ "$launch_query_status" = 113 ] || { WHY="无法确认 launchd 服务 $1 的状态（查询退出码 ${launch_query_status}）"; return 1; }
  fi
  return 0
}

stop_server() {
  WHY=""
  case $SERVICE in
    2)
      if [ -f "$DIR/server.pid" ]; then
        stop_pid=$(cat "$DIR/server.pid") || { WHY="读不出后台服务的 PID"; return 1; }
        case $stop_pid in ''|*[!0-9]*) WHY="后台服务 PID 无效，保留 server.pid"; return 1 ;; esac
        [ "$stop_pid" -gt 1 ] || { WHY="后台服务 PID 无效，保留 server.pid"; return 1; }
        if process_running "$stop_pid"; then
          kill "$stop_pid" 2>/dev/null || { WHY="无法停止后台进程 $stop_pid"; return 1; }
          wait_stopped "$stop_pid" || return 1
        fi
        rm -f -- "$DIR/server.pid" || return 1
      fi ;;
    1)
      if [ "$(uname -s)" = Darwin ]; then
        bootout_job com.vorto.vortoone.server || return 1
      else
        systemctl --user stop vortoone.service >/dev/null 2>&1 || { WHY="systemd 没有完成停止服务"; return 1; }
        stopped_state=$(systemctl --user show vortoone.service -p ActiveState --value 2>/dev/null) \
          || { WHY="无法确认 systemd 服务已停止"; return 1; }
        stopped_pid=$(systemctl --user show vortoone.service -p MainPID --value 2>/dev/null) \
          || { WHY="无法确认 systemd 服务进程已退出"; return 1; }
        case "$stopped_state:$stopped_pid" in inactive:0|failed:0) ;; *) WHY="systemd 服务仍未停止（${stopped_state}，PID ${stopped_pid}）"; return 1 ;; esac
      fi ;;
  esac
  return 0
}

install_service() {
  if [ "$(uname -s)" = Darwin ]; then
    agents="$HOME/Library/LaunchAgents"
    mkdir -p "$agents" || return 1
    cat > "$agents/com.vorto.vortoone.server.plist" <<EOF || return 1
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
    cat > "$agents/com.vorto.vortoone.backup.plist" <<EOF || return 1
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.vorto.vortoone.backup</string>
  <key>ProgramArguments</key><array><string>$DIR/backup.sh</string></array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>30</integer></dict>
  <key>StandardOutPath</key><string>$DIR/backup.log</string>
  <key>StandardErrorPath</key><string>$DIR/backup.log</string>
</dict>
</plist>
EOF
    for label in server backup; do
      bootout_job "com.vorto.vortoone.$label" || return 1
      launchctl bootstrap "gui/$(id -u)" "$agents/com.vorto.vortoone.$label.plist" \
        || { WHY="launchd 没有启动 $label"; return 1; }
    done
  else
    command -v systemctl >/dev/null 2>&1 || { WHY="没有 systemd：加 --background 或 --no-service"; return 1; }
    units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    mkdir -p "$units" || return 1
    cat > "$units/vortoone.service" <<EOF || return 1
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
    cat > "$units/vortoone-backup.service" <<EOF || return 1
[Unit]
Description=VortoOne daily backup

[Service]
Type=oneshot
ExecStart=$DIR/backup.sh
UMask=0077
Nice=10
StandardOutput=append:$DIR/backup.log
StandardError=append:$DIR/backup.log
EOF
    cat > "$units/vortoone-backup.timer" <<EOF || return 1
[Unit]
Description=VortoOne daily backup

[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true

[Install]
WantedBy=timers.target
EOF
    systemctl --user daemon-reload || { WHY="systemd 重载服务配置失败"; return 1; }
    systemctl --user enable --now vortoone-backup.timer >/dev/null || { WHY="systemd 登记备份定时器失败"; return 1; }
    systemctl --user enable vortoone.service >/dev/null || { WHY="systemd 登记服务失败"; return 1; }
    systemctl --user restart vortoone.service || { WHY="systemd 启动服务失败"; return 1; }
    if command -v loginctl >/dev/null 2>&1 && [ "$(loginctl show-user "$(id -un)" -p Linger --value 2>/dev/null)" != yes ]; then
      say "提示：要让它在你没登录时也运行，跑一次 sudo loginctl enable-linger $(id -un)"
    fi
  fi
}

main "$@"
