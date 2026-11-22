import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

from fastapi.testclient import TestClient

from src.scheduling.notices import record_notice
from src.utils.state_dir import state_dir
from tests.unit.test_member_agents import ORIGIN, TOKEN, _join, home  # noqa: F401 - pytest fixture


def _seed(home):
    root = home["root"]
    ledger = state_dir(root) / "logs" / "notices.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    legacy = [{"ts": "2026-09-01T12:00:00Z", "source": "cron:qa", "text": text}
              for text in ("first delivery", "second delivery")]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in legacy), encoding="utf-8")
    owner = home["owner"]
    url = f"{home['base']}/notifications"
    response = owner.get(url, params={"limit": 1})
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    page = response.json()
    assert page["counts"] == {"all": 2, "unread": 0, "waiting": 0, "mentions": 0, "reactions": 0}
    assert page["items"][0]["latest"]["text"] == "second delivery"
    later_page = owner.get(url, params={"limit": 1, "cursor": page["nextCursor"]}).json()
    assert later_page["nextCursor"] is None
    assert later_page["items"][0]["latest"]["text"] == "first delivery"
    assert not (state_dir(root) / "logs" / "notice-inbox.json").exists()
    return owner, url, page["items"][0]["id"], later_page["items"][0]["id"]


def test_new_scheduler_notices_have_distinct_ids_and_start_unread(home):
    owner, url, _, _ = _seed(home)
    for _ in range(2):
        record_notice(home["root"], "identical delivery", source="cron:qa")
    items = owner.get(url, params={"filter": "unread"}).json()["items"]
    assert len(items) == 2 and len({item["id"] for item in items}) == 2
    assert {item["latest"]["text"] for item in items} == {"identical delivery"}
    assert owner.get(f"{url}/count").json()["unread"] == 2
    assert owner.post(f"{url}/read-all", json={}).json() == {"ids": [item["id"] for item in items]}
    assert owner.get(f"{url}/count").json()["unread"] == 0


def test_notice_actions_update_inbox_and_preserve_the_original_ledger(home):
    owner, url, added, older = _seed(home)
    read, done, snooze = (f"{url}/{added}/{action}" for action in ("read", "done", "snooze"))
    ledger = state_dir(home["root"]) / "logs" / "notices.jsonl"
    original = ledger.read_bytes()

    unread = owner.post(read, json={"read": False})
    assert unread.status_code == 200 and unread.json()["readAt"] is None
    assert [row["id"] for row in owner.get(url, params={"filter": "unread"}).json()["items"]] == [added]
    assert owner.get(f"{url}/count").json() == {"unread": 1, "waiting": 0}
    now = datetime.now(timezone.utc)
    snoozed = owner.post(snooze, json={"until": (now + timedelta(hours=1)).isoformat()})
    assert snoozed.status_code == 200 and snoozed.json()["snoozedUntil"].endswith("Z")
    assert owner.get(f"{url}/count").json()["unread"] == 0
    assert owner.post(f"{url}/done-all", json={"filter": "all"}).json() == {"ids": [older]}
    assert owner.get(url, params={"filter": "all"}).json()["items"] == []
    assert owner.post(snooze, json={"until": (now - timedelta(hours=1)).isoformat()}).status_code == 200
    assert owner.get(f"{url}/count").json()["unread"] == 1

    assert owner.post(f"{url}/read-all", json={"filter": "mentions"}).json() == {"ids": []}
    assert owner.get(f"{url}/count").json()["unread"] == 1
    assert owner.post(f"{url}/read-all", json={"filter": "unread"}).json() == {"ids": [added]}
    assert owner.post(f"{url}/read-all", json={}).json() == {"ids": []}
    assert owner.post(snooze, json={"until": None}).json()["snoozedUntil"] is None

    owner.post(read, json={"read": False}).raise_for_status()
    finished = owner.post(done, json={"done": True}).json()
    assert finished["doneAt"] and finished["readAt"]
    assert owner.post(done, json={"done": True}).json() == finished
    done_page = owner.get(url, params={"filter": "done"}).json()
    assert {row["id"] for row in done_page["items"]} == {added, older}
    assert done_page["counts"]["all"] == 0
    assert owner.post(done, json={"done": False}).json()["doneAt"] is None
    fresh_client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
    assert [row["id"] for row in fresh_client.get(url).json()["items"]] == [added]
    assert ledger.read_bytes() == original


def test_notice_routes_refuse_other_actors_and_invalid_inputs(home):
    owner, url, added, _ = _seed(home)
    member = _join(home)
    assert member.get(url).json()["items"] == []
    assert member.get(f"{url}/count").json() == {"unread": 0, "waiting": 0}

    anonymous = TestClient(home["app"])
    assert anonymous.get(url).status_code == 401
    for tail, body in (("read-all", {}), ("done-all", {}), (f"{added}/done", {"done": True}),
                       (f"{added}/read", {"read": False}), (f"{added}/snooze", {"until": None})):
        assert member.post(f"{url}/{tail}", json=body).status_code == 403
        assert anonymous.post(f"{url}/{tail}", json=body).status_code == 401

    browser = TestClient(home["app"])
    assert browser.post("/api/auth/login", json={"token": TOKEN}).status_code == 200
    assert browser.post(f"{url}/{added}/read", json={"read": False}, headers={"Origin": "https://other.test"}).status_code == 403
    assert browser.post(f"{url}/{added}/read", json={"read": False}, headers=ORIGIN).status_code == 200
    assert owner.post(f"{url}/missing/read", json={"read": True}).status_code == 404
    assert owner.post(f"{url}/missing/done", json={"done": True}).status_code == 404
    assert owner.post(f"{url}/missing/snooze", json={"until": None}).status_code == 404
    assert owner.post(f"{url}/{added}/read", json={"read": "false"}).status_code == 422
    assert owner.post(f"{url}/{added}/snooze", json={"until": "tomorrow"}).status_code == 400
    assert owner.post(f"{url}/read-all", json={"filter": "wrong"}).status_code == 422
    assert owner.get(url, params={"cursor": -1}).status_code == 422
    assert owner.post(f"{url}/done-all".replace(home["base"], "/api/app/organizations/other"),
                      json={}).status_code == 404
    assert owner.get(f"{url}/count").json()["unread"] == 1


def test_inbox_state_errors_are_visible_and_never_overwrite_the_bad_file(home):
    owner, url, added, _ = _seed(home)
    path = state_dir(home["root"]) / "logs" / "notice-inbox.json"
    path.write_text("broken", encoding="utf-8")
    assert owner.get(url).status_code == 409
    assert owner.get(f"{url}/count").status_code == 409
    assert owner.post(f"{url}/{added}/read", json={"read": False}).status_code == 409
    assert path.read_text(encoding="utf-8") == "broken"


def test_concurrent_inbox_writes_keep_changes_to_both_notices(home):
    owner, url, first, second = _seed(home)
    start = Barrier(2)

    def mark(notice_id):
        client = TestClient(home["app"], headers={"Authorization": f"Bearer {TOKEN}"})
        start.wait(timeout=5)
        return client.post(f"{url}/{notice_id}/read", json={"read": False})

    with ThreadPoolExecutor(max_workers=2) as workers:
        responses = list(workers.map(mark, (first, second)))
    assert [response.status_code for response in responses] == [200, 200]
    assert owner.get(f"{url}/count").json()["unread"] == 2
