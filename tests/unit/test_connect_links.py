"""新界面的「连接电脑」（2026-11-25，主人选「接通新界面的连接对话框」）。

链接就是现有的配对码：在桌面 App 里打开（vortoone://pair）或在目标电脑的 VortoOne 菜单里填。
连接电脑线里还做不到的那些如实回答：不认识的电脑 404，认识的电脑上做不到的 409，本来就空的回真实的空。
"""
import re

from tests.unit.test_computer_web import (  # noqa: F401 - pytest fixture
    ORIGIN, browser, claim_native_home, exchange, home, join_member,
)

COMPUTER = "c" * 32


def _link(client, home, kind="computer"):
    made = client.post(home["base"] + "/devices/connect-links", json={"kind": kind})
    assert made.status_code == 201, made.text
    return made.json()


def _state(client, home, link):
    answer = client.get(f"{home['base']}/devices/connect-links/{link['id']}")
    assert answer.status_code == 200, answer.text
    return answer.json()


def _pairing(home, link):
    return {"home_instance_id": home["store"].identity()["home_instance_id"], "code": link["code"]}


def test_a_link_is_a_pairing_code_and_follows_the_computer_that_uses_it(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    link = _link(owner, home)
    assert re.fullmatch(r"[A-Z2-7]{12}", link["code"]) and link["expiresAt"].endswith("Z")
    assert link["commands"]["unix"].startswith("# ") and link["code"] in link["commands"]["unix"]
    assert link["commands"]["windows"].startswith("REM ")                          # 只是说明，粘贴进去什么都不执行
    assert _state(owner, home, link) == {"status": "waiting"}
    assert exchange(home, _pairing(home, link), COMPUTER, "工作电脑").status_code == 200
    assert _state(owner, home, link) == {"status": "joined", "machineId": COMPUTER}
    again = _link(owner, home)
    replaced = _link(owner, home)                                                  # 新码替换旧码
    assert _state(owner, home, again) == {"status": "expired"}
    assert _state(owner, home, replaced)["status"] == "waiting"
    assert owner.get(home["base"] + "/devices/connect-links/not-a-link").status_code == 404
    assert owner.post(home["base"] + "/devices/connect-links", json={"kind": "phone"}).status_code == 400


def test_a_members_link_only_counts_their_own_computer(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    alice, _ = join_member(home, owner, "alice@example.test")
    hers = _link(alice, home)
    mine = _link(owner, home)
    assert exchange(home, _pairing(home, mine), COMPUTER, "所有者的电脑").status_code == 200
    assert _state(alice, home, hers) == {"status": "waiting"}                     # 所有者配上的不算她的
    assert exchange(home, _pairing(home, hers), "d" * 32, "Alice 的电脑").status_code == 200
    assert _state(alice, home, hers) == {"status": "joined", "machineId": "d" * 32}


def test_what_a_connected_computer_cannot_do_yet_is_said_plainly(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    link = _link(owner, home)
    assert exchange(home, _pairing(home, link), COMPUTER, "工作电脑").status_code == 200
    base, mine, other = home["base"], f"{home['base']}/machines/{COMPUTER}", f"{home['base']}/machines/{'e' * 32}"
    for method, path in (("POST", "/access/allow"), ("POST", "/cli-install/ask"), ("POST", "/move-agents"),
                         ("POST", "/pause"), ("POST", "/clis/check"), ("POST", "/account-logins")):
        refused = owner.request(method, mine + path, json={})
        assert (refused.status_code, refused.json()["error"]["code"]) == (409, "computer.unsupported"), path
        assert owner.request(method, other + path, json={}).status_code == 404, path            # 不认识的电脑
    for path in ("/phone-looks", "/xcode-open", "/wifi-pairings"):
        assert owner.post(f"{base}/devices/computers/{COMPUTER}{path}", json={}).status_code == 409, path
    assert owner.get(f"{base}/devices/computers/{COMPUTER}/wifi-iphones").status_code == 409
    assert owner.get(mine + "/cli-install?tool=codex").json() == {"install": None}
    assert owner.request("DELETE", mine + "/cli-install?tool=codex").json() == {"install": None}
    assert owner.post(mine + "/cli-install", json={"tool": "codex"}).status_code == 409
    assert owner.get(mine + "/queue").json() == {"machineId": COMPUTER, "name": "工作电脑", "working": [], "waiting": []}
    for method, path in (("GET", f"{mine}/account-logins/x"), ("DELETE", f"{mine}/accounts/x"),
                         ("PATCH", f"{base}/devices/phones/p"), ("POST", f"{base}/devices/phones/p/stop"),
                         ("POST", f"{base}/devices/requests/r/approve"), ("POST", f"{base}/devices/access-requests/r/decide"),
                         ("GET", f"{base}/machines/host"), ("POST", f"{base}/machines/device/ABCD/confirm"),
                         ("GET", "/api/app/machines/device/ABCD"), ("GET", f"{base}/browser/handoffs/h"),
                         ("POST", f"{base}/browser-profiles/p/open"), ("PUT", f"{base}/ai-accounts/a/serves")):
        assert owner.request(method, path, json={} if method != "GET" else None).status_code == 404, path
    assert owner.post(f"{base}/machines/host", json={}).json()["error"]["code"] == "computer.host_unavailable"
    assert owner.get(f"{base}/browser-profiles").json() == {"profiles": []}
    assert owner.post(f"{base}/browser-profiles", json={}).status_code == 409
    assert owner.get(f"{base}/ai-accounts").json() == {"accounts": [], "computers": []}
    assert owner.post(f"{base}/ai-accounts/refresh", json={}).status_code == 204


def test_members_reach_only_their_own_computers(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    link = _link(owner, home)
    assert exchange(home, _pairing(home, link), COMPUTER, "所有者的电脑").status_code == 200
    alice, _ = join_member(home, owner, "alice@example.test")
    assert alice.post(f"{home['base']}/machines/{COMPUTER}/pause", json={}).status_code == 404   # 不是她配的
    assert alice.get(f"{home['base']}/ai-accounts").json()["accounts"] == []


def test_a_used_link_shows_expired_after_removal_or_a_restart_never_a_wrong_computer(home, monkeypatch):
    from src.gateway import connected_computers
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    link = _link(owner, home)
    assert exchange(home, _pairing(home, link), COMPUTER, "工作电脑").status_code == 200
    assert owner.request("DELETE", f"{home['base']}/devices/computers/{COMPUTER}").status_code == 204
    assert _state(owner, home, link) == {"status": "expired"}                     # 配上以后又撤掉了
    second = _link(owner, home)
    assert exchange(home, _pairing(home, second), "f" * 32, "另一台").status_code == 200
    connected_computers._USED_PAIRINGS.clear()                                     # 服务重启：进程里的记录没了
    assert _state(owner, home, second) == {"status": "expired"}                    # 不猜，免得算错电脑


def test_a_waiting_link_expires_with_its_code_and_others_cannot_read_it(home, monkeypatch):
    from src.gateway.connected_computers import ConnectedComputers
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    link = _link(owner, home)
    pairing_id, expires = link["id"].split(".")
    later = ConnectedComputers(home["root"], clock=lambda: int(expires) + 1)
    assert later.link_state(pairing_id, int(expires)) == ("expired", None)
    assert exchange(home, _pairing(home, link), COMPUTER, "工作电脑").status_code == 200
    alice, _ = join_member(home, owner, "alice@example.test")
    assert _state(alice, home, link) == {"status": "expired"}                      # 别人的链接：看不到配上了哪台


def test_a_removed_computer_cannot_be_acted_on(home, monkeypatch):
    claim_native_home(home, monkeypatch)
    owner = browser(home, owner=True)
    link = _link(owner, home)
    assert exchange(home, _pairing(home, link), COMPUTER, "工作电脑").status_code == 200
    assert owner.request("DELETE", f"{home['base']}/devices/computers/{COMPUTER}").status_code == 204
    assert owner.post(f"{home['base']}/machines/{COMPUTER}/pause", json={}).status_code == 404
    assert owner.get(f"{home['base']}/machines/{COMPUTER}/queue").status_code == 404
