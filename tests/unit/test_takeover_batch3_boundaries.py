"""接管 Codex 草稿第三批时补的：日历、界面偏好按人隔离，别的站点借不走主人的登录。"""
import pytest
from fastapi.testclient import TestClient

from src.web.auth import SESSION_COOKIE
from tests.unit.test_member_login import TOKEN, _join, home  # noqa: F401 - fixture

EVENT = {"title": "体检", "start": "2026-12-01T09:00:00+08:00", "end": "2026-12-01T10:00:00+08:00"}
RANGE = {"from": "2026-11-30T00:00:00+00:00", "to": "2026-12-03T00:00:00+00:00"}


def test_private_events_are_seen_and_changed_only_by_their_person(home):
    base = home["base"]
    ana, ben = _join(home), _join(home, email="ben@example.com", name="小本")
    owner = home["owner"]
    mine = owner.post(f"{base}/calendar/events", json=EVENT)
    assert mine.status_code == 201, mine.text
    path = f"{base}/calendar/events/{mine.json()['id']}"
    for other in (ana, ben):
        assert other.get(f"{base}/calendar", params=RANGE).json()["events"] == []
        assert other.patch(path, json={"title": "改掉"}).status_code == 404
        assert other.delete(path).status_code == 404
        assert other.delete(path, params={"scope": "series"}).status_code == 404     # 不能借报错探出别人的日程在不在
    assert [event["title"] for event in owner.get(f"{base}/calendar", params=RANGE).json()["events"]] == ["体检"]


def test_one_persons_display_choices_do_not_change_anothers(home):
    base = home["base"]
    member = _join(home)
    assert member.put(f"{base}/me/activity", json={"show": False}).status_code == 200
    shown = lambda client: client.get("/api/app/me").json()["organizations"][0]["showActivity"]
    assert (shown(member), shown(home["owner"])) == (False, True)


ENTRIES = [
    ("GET", "{base}/calendar", None),
    ("POST", "{base}/calendar/events", EVENT),
    ("PATCH", "{base}/calendar/events/x", {"title": "改"}),
    ("DELETE", "{base}/calendar/events/x", None),
    ("PUT", "{base}/me/activity", {"show": False}),
    ("PUT", "{base}/me/start", {"page": "home"}),
    ("PUT", "{base}/me/chat", {"textSize": "lg"}),
]


def _hit(client, home, method, path, body, **kwargs):
    return client.request(method, path.format(base=home["base"]), **({} if body is None else {"json": body}), **kwargs)


@pytest.mark.parametrize("method,path,body", [*ENTRIES, ("GET", "{base}/mail/providers", None)])
def test_nobody_can_use_them_anonymously(home, method, path, body):
    assert _hit(TestClient(home["app"]), home, method, path, body).status_code in {401, 403}


@pytest.mark.parametrize("method,path,body", ENTRIES)
def test_another_site_cannot_borrow_the_owners_cookie(home, method, path, body):
    borrowed = TestClient(home["app"])
    borrowed.cookies.set(SESSION_COOKIE, TOKEN)
    refused = _hit(borrowed, home, method, path, body, headers={"Origin": "https://evil.example"})
    assert refused.status_code in {401, 403}, refused.text
