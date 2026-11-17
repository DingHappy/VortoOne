#!/bin/sh
# 冒烟：安装命令的升级与失败退回（deploy/install.sh）。用当前源码做三版发布文件，在一个临时目录里：
#   1. 装第一版（后台启动），认领所有者；
#   2. 升级到第二版：要先备份、自检通过、upgrade.json 记成功；
#   3. 第三版先改坏所有者、JSON 和 SQLite 数据再退出：要退回第二版和升级前的数据，所有者照样能登录，upgrade.json 记原因。
#
#   scripts/check-server-upgrade.sh [端口]
#
# 界面要先构建好（frontend/apps/web/dist）。只在本机临时目录里装，不登记任何服务。
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PORT=${1:-18557}
WORK=$(mktemp -d "${TMPDIR:-/tmp}/vortoone-upgrade.XXXXXX")
INST="$WORK/inst"
ORIGIN="http://127.0.0.1:$PORT"
base=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$ROOT/pyproject.toml" | head -1)
v1="$base" v2="$base.post1" v3="$base.post2"

cleanup() {
  [ -f "$INST/server.pid" ] && kill "$(cat "$INST/server.pid")" 2>/dev/null || true
}
trap cleanup EXIT

fail() { echo "✗ $*" >&2; [ -f "$INST/server.log" ] && tail -20 "$INST/server.log" >&2; exit 1; }

release() {   # release <版本> [broken]
  src="$WORK/src-$1"
  mkdir -p "$src/frontend/apps/web" "$src/scripts" "$src/deploy"
  cp -R "$ROOT/src" "$ROOT/pyproject.toml" "$ROOT/README.md" "$ROOT/constraints.txt" "$src/"
  cp -R "$ROOT/frontend/apps/web/dist" "$src/frontend/apps/web/"
  cp "$ROOT/scripts/build-server-release.sh" "$src/scripts/"
  cp "$ROOT/deploy/install.sh" "$src/deploy/"
  sed "s/^version = \".*\"$/version = \"$1\"/" "$ROOT/pyproject.toml" > "$src/pyproject.toml"
  if [ "${2:-}" = broken ]; then
    cat >> "$src/src/web/server.py" <<'PYFAULT'

# 仅存在于这个临时发布文件中的故障注入：先写坏数据，再模拟升级启动失败。
from pathlib import Path as _FaultPath
import sqlite3 as _fault_sqlite
from src.workflow.members.owner_account import OwnerAccount as _FaultOwner
_fault_state = _FaultPath.cwd() / ".vortoone"
(_fault_state / "upgrade-proof.json").write_text('{"value":"changed-by-broken-version"}')
_fault_db = _fault_sqlite.connect(_fault_state / "upgrade-proof.db")
_fault_db.execute("update proof set value = 'changed-by-broken-version'")
_fault_db.commit()
_fault_db.close()
_FaultOwner(str(_FaultPath.cwd())).path.write_text('{"broken":')
(_fault_state / "upgrade-fault-ran").write_text("yes")
raise SystemExit("broken on purpose after changing data")
PYFAULT
  fi
  "$src/scripts/build-server-release.sh" "$WORK/rel-$1" >/dev/null
}

running() { curl -fsS "$ORIGIN/api/instance" | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'; }
field() { sed -n "s/.*\"$1\": *\([^,}]*\).*/\1/p" "$INST/upgrade.json" | tr -d '"'; }
sign_in() {
  curl -s -o /dev/null -w '%{http_code}' -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
    -d '{"email":"me@example.com","password":"correct horse battery"}' "$ORIGIN/api/auth/sign-in/email"
}

release "$v1"; release "$v2"; release "$v3" broken
echo "· 三版发布文件：$v1 $v2 ${v3}（坏的）"

sh "$ROOT/deploy/install.sh" --from "$WORK/rel-$v1" --dir "$INST" --port "$PORT" --background >/dev/null
[ "$(running)" = "$v1" ] || fail "第一版没起来"
token=$(cd "$INST/home" && "$INST/current/bin/vc" owner-claim --url "$ORIGIN" --reset | sed -n 's/.*claim=\([^"]*\)".*/\1/p')
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Origin: $ORIGIN" -H "Content-Type: application/json" \
  -d "{\"token\":\"$token\",\"name\":\"Ding\",\"email\":\"me@example.com\",\"password\":\"correct horse battery\"}" \
  "$ORIGIN/api/auth/owner-claim")
[ "$code" = 200 ] || fail "认领失败（$code）"
echo "✓ 装好 $v1 并认领"

sh "$ROOT/deploy/install.sh" --from "$WORK/rel-$v2" --dir "$INST" --background >/dev/null || fail "升级到 $v2 失败"
[ "$(running)" = "$v2" ] || fail "升级后跑的不是 $v2"
[ "$(field ok)" = true ] && [ "$(field from)" = "$v1" ] || fail "upgrade.json 没记成功"
ls "$INST/backups"/vortoone-backup-*-upgrade.tar.gz >/dev/null 2>&1 || fail "升级前没有备份"
[ "$(sign_in)" = 200 ] || fail "升级后所有者登录不了"
grep -q "升级到 $v2" "$INST/home/.vortoone/logs/notices.jsonl" 2>/dev/null || fail "升级成功没有进所有者的通知"
echo "✓ 升级到 ${v2}：先备份、自检通过，通知里有"

"$INST/current/bin/python" - "$INST/home/.vortoone" <<'PYPROOF'
import json, sqlite3, sys
from pathlib import Path
state = Path(sys.argv[1])
(state / "upgrade-proof.json").write_text(json.dumps({"value": "before-upgrade"}))
with sqlite3.connect(state / "upgrade-proof.db") as db:
    db.execute("create table proof (value text)")
    db.execute("insert into proof values ('before-upgrade')")
PYPROOF

if sh "$ROOT/deploy/install.sh" --from "$WORK/rel-$v3" --dir "$INST" --background >/dev/null 2>&1; then
  fail "坏掉的 $v3 居然升级成功了"
fi
[ "$(readlink "$INST/current")" = "versions/$v2" ] || fail "没有退回 $v2"
[ "$(running)" = "$v2" ] || fail "退回后服务没有跑 $v2"
[ "$(field ok)" = false ] && [ -n "$(field reason)" ] || fail "upgrade.json 没记失败原因"
[ "$(sign_in)" = 200 ] || fail "退回后所有者登录不了"
"$INST/current/bin/python" - "$INST/home" <<'PYVERIFY'
import json, sqlite3, sys
from pathlib import Path
home = Path(sys.argv[1])
state = home / ".vortoone"
assert json.loads((state / "upgrade-proof.json").read_text())["value"] == "before-upgrade"
with sqlite3.connect(state / "upgrade-proof.db") as db:
    assert db.execute("select value from proof").fetchone() == ("before-upgrade",)
assert not (state / "upgrade-fault-ran").exists()
assert any((aside / "upgrade-fault-ran").exists() for aside in home.glob(".vortoone.before-restore-*")), \
    "故障版本必须实际改过数据，不能只验证版本指向"
PYVERIFY
grep -q "没有成功" "$INST/home/.vortoone/logs/notices.jsonl" 2>/dev/null || fail "退回后所有者的通知里没有说明"
echo "✓ 升级到坏的 ${v3}：退回 ${v2}，原因：$(field reason)；所有者的通知里有说明"
