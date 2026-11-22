"""在册成员推荐人选，所有者通过现有邮箱邀请核对入会。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from src.workflow.members import registry
from tests.unit.test_member_login import ORIGIN, PASSWORD, TOKEN, _group, _invite, _join, home  # noqa: F401


def _suggest(member, base, email, **more):
    return member.post(f"{base}/join-requests/suggest", json={"email": email, **more})


def test_member_suggestion_can_only_be_reviewed_as_a_default_email_invitation(home):
    owner, base = home["owner"], home["base"]
    member = _join(home, "member@example.com", "推荐人")
    initial_members = owner.get(f"{base}/members").json()
    made = _suggest(member, base, " Visitor@Example.COM ", note=" 有设计经验 ")
    assert made.status_code == 200 and made.headers["cache-control"] == "no-store"
    suggestion_id = made.json()["id"]
    assert _suggest(member, base, "visitor@example.com", note="有设计经验").json() == {"id": suggestion_id}
    duplicate = _suggest(member, base, "visitor@example.com", note="另一份备注")
    assert duplicate.status_code == 409 and duplicate.json()["error"]["code"] == "join.waiting"

    mine = member.get(f"{base}/join-requests/mine")
    assert mine.status_code == 200 and mine.headers["cache-control"] == "no-store"
    [row] = mine.json()["suggestions"]
    assert row == {"id": suggestion_id, "email": "visitor@example.com", "note": "有设计经验",
                   "createdAt": row["createdAt"]}
    review = owner.get(f"{base}/join-requests")
    assert review.status_code == 200 and review.headers["cache-control"] == "no-store"
    [request] = review.json()["requests"]
    assert request == {**row, "name": "visitor@example.com", "verified": False, "confirmed": False,
                       "suggestedBy": "推荐人", "link": None, "expiresAt": request["expiresAt"]}
    assert request["expiresAt"] > row["createdAt"]
    assert owner.get(f"{base}/invitations").json() == {"invitations": []}

    approve = f"{base}/join-requests/{suggestion_id}/approve"
    for grant in ({"roleIds": ["admins"]}, {"channelIds": ["channel"]}, {"agentIds": ["agent"]}):
        assert owner.post(approve, json=grant).status_code == 400
    assert owner.get(f"{base}/invitations").json() == {"invitations": []}
    approved = owner.post(approve, json={"roleIds": [], "channelIds": [], "agentIds": []})
    assert approved.status_code == 200 and approved.headers["cache-control"] == "no-store"
    outcome = approved.json()
    assert outcome == {"outcome": "invited", "url": outcome["url"], "emailed": False}
    assert owner.get(f"{base}/members").json() == initial_members
    assert member.get(f"{base}/join-requests/mine").json() == {"suggestions": []}
    gone = owner.post(approve, json={})
    assert gone.status_code == 409 and gone.json()["error"]["code"] == "join.answered"
    [invitation] = owner.get(f"{base}/invitations").json()["invitations"]
    assert invitation["email"] == "visitor@example.com" and invitation["roleIds"] == []

    guest = TestClient(home["app"], headers=ORIGIN)
    token = outcome["url"].rsplit("/", 1)[1]
    preview_path = f"/api/app/invitations/{token}"
    preview = guest.get(preview_path).json()
    assert preview["email"] == "visitor@example.com"
    assert preview["kind"] == "email" and preview["roles"] == [] and "note" not in preview
    wrong = guest.post("/api/auth/sign-up/email", headers={"x-invitation-token": token},
                       json={"name": "别的账号", "email": "wrong@example.com", "password": PASSWORD})
    assert wrong.status_code == 403
    assert guest.post("/api/auth/sign-up/email", headers={"x-invitation-token": token},
                      json={"name": "来访者", "email": "visitor@example.com", "password": PASSWORD}).status_code == 200
    assert owner.get(f"{base}/members").json() == initial_members
    assert guest.post(f"{preview_path}/accept", json={}).json() == {"orgId": home["org"]}
    assert guest.get(preview_path).status_code == 404
    assert guest.get("/api/app/me").json()["organizations"][0]["role"] == "member"


def test_members_can_read_and_cancel_only_their_own_suggestions(home):
    owner, base = home["owner"], home["base"]
    ana = _join(home, "ana@example.com")
    bai = _join(home, "bai@example.com")
    made = _suggest(ana, base, "hidden@example.com", note="联系人备注").json()["id"]
    path = f"{base}/join-requests/{made}"
    assert bai.get(f"{base}/join-requests/mine").json() == {"suggestions": []}
    assert bai.delete(path).status_code == 404
    assert bai.get(f"{base}/join-requests").status_code == 403
    assert bai.post(f"{path}/approve", json={}).status_code == 403
    assert bai.post(f"{path}/deny", json={}).status_code == 403
    assert owner.delete(path).status_code == 403
    assert owner.get(f"{base}/join-requests/mine").status_code == 403
    assert _suggest(owner, base, "hidden@example.com").status_code == 403
    assert ana.delete(path, headers={"Origin": "http://foreign.example"}).status_code == 403

    cancelled = ana.delete(path)
    assert cancelled.status_code == 204 and cancelled.headers["cache-control"] == "no-store"
    assert ana.delete(path).status_code == 404
    assert ana.get(f"{base}/join-requests/mine").json() == {"suggestions": []}
    assert owner.get(f"{base}/join-requests").json() == {"requests": []}
    assert owner.get(f"{base}/invitations").json() == {"invitations": []}


def test_owner_review_proof_and_denial_without_outbound_email(home, monkeypatch):
    from src.web.auth import SESSION_COOKIE

    owner, base = home["owner"], home["base"]
    member = _join(home)
    suggestion_id = _suggest(member, base, "invitee@example.com").json()["id"]
    deny = f"{base}/join-requests/{suggestion_id}/deny"
    assert owner.post(deny, json={"reason": "要发给对方的理由"}).status_code == 400
    assert len(owner.get(f"{base}/join-requests").json()["requests"]) == 1

    browser = TestClient(home["app"])
    browser.cookies.set(SESSION_COOKIE, TOKEN)
    assert browser.get(f"{base}/join-requests").status_code == 200
    assert browser.get(f"{base}/join-requests", headers={"Origin": "http://foreign.example"}).status_code == 403
    assert browser.post(deny, json={}).status_code == 403
    assert browser.post(deny, headers={"Origin": "http://foreign.example"}, json={}).status_code == 403
    denied = browser.post(deny, headers=ORIGIN, json={})
    assert denied.status_code == 204 and denied.headers["cache-control"] == "no-store"
    assert owner.post(deny, json={}).json()["error"]["code"] == "join.answered"
    assert member.get(f"{base}/join-requests/mine").json() == {"suggestions": []}
    assert owner.get(f"{base}/invitations").json() == {"invitations": []}

    monkeypatch.delenv("VORTOONE_API_TOKEN")
    assert browser.get(f"{base}/join-requests").status_code == 403


def test_guests_and_inviters_cannot_submit_suggestions(home):
    owner, base = home["owner"], home["base"]
    browser = TestClient(home["app"], headers=ORIGIN)
    assert _suggest(browser, base, "other@example.com").status_code == 401
    token = _invite(home, "pending@example.com")
    signed = browser.post("/api/auth/sign-up/email", headers={"x-invitation-token": token},
                          json={"name": "尚未加入", "email": "pending@example.com", "password": PASSWORD})
    assert signed.status_code == 200
    assert _suggest(browser, base, "other@example.com").status_code == 403

    invite_role = _group(home, "负责邀请", ["invite_people"])
    recruiter = _join(home, "recruiter@example.com", roleIds=[invite_role])
    assert _suggest(recruiter, base, "other@example.com").status_code == 403
    assert not owner.get(f"{base}/join-requests").json()["requests"]
    member = _join(home, "accepted@example.com")
    assert _suggest(member, base.replace(home["org"], "foreign-org"), "other@example.com").status_code == 404
    assert _suggest(member, base, "bad-email").json()["error"]["code"] == "join.invalid_email"
    assert _suggest(member, base, "other@example.com", note="x" * 201).status_code == 400
    assert _suggest(member, base, "other@example.com", roleIds=["admins"]).status_code == 400


def test_expiry_removal_and_pending_limits(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    ana = _join(home, "ana@example.com")
    bai = _join(home, "bai@example.com")
    chen = _join(home, "chen@example.com")
    monkeypatch.setattr(registry, "_MAX_JOIN_SUGGESTIONS_PER_MEMBER", 1)
    monkeypatch.setattr(registry, "_MAX_JOIN_SUGGESTIONS", 2)
    first = _suggest(ana, base, "one@example.com").json()["id"]
    extra = _suggest(ana, base, "two@example.com")
    assert extra.status_code == 409 and extra.json()["error"]["code"] == "join.full"
    assert _suggest(bai, base, "two@example.com").status_code == 200
    full = _suggest(chen, base, "three@example.com")
    assert full.status_code == 409 and full.json()["error"]["code"] == "join.full"

    member_id = ana.get("/api/app/me").json()["user"]["id"]
    assert owner.delete(f"{base}/members/{member_id}").status_code == 204
    assert _suggest(ana, base, "three@example.com").status_code == 401
    assert owner.post(f"{base}/join-requests/{first}/approve", json={}).status_code == 409
    assert first not in registry.MemberRegistry(home["root"]).snapshot()["join_suggestions"]

    future = datetime.now(timezone.utc) + timedelta(days=8)
    monkeypatch.setattr(registry, "_now", lambda: future)
    assert owner.get(f"{base}/join-requests").json() == {"requests": []}
    assert bai.get(f"{base}/join-requests/mine").json() == {"suggestions": []}
    assert _suggest(bai, base, "three@example.com").status_code == 200
    assert len(registry.MemberRegistry(home["root"]).snapshot()["join_suggestions"]) == 1


def test_approval_is_serialized_and_does_not_replace_an_existing_invitation(home):
    owner, base = home["owner"], home["base"]
    ana = _join(home, "ana@example.com")
    bai = _join(home, "bai@example.com")
    first = _suggest(ana, base, "new@example.com").json()["id"]
    second = _suggest(bai, base, "new@example.com").json()["id"]
    approve = f"{base}/join-requests/{first}/approve"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: owner.post(approve, json={}), range(2)))
    assert sorted(result.status_code for result in responses) == [200, 409]
    approved = next(result for result in responses if result.status_code == 200).json()
    redundant = owner.post(f"{base}/join-requests/{second}/approve", json={})
    assert redundant.status_code == 409 and redundant.json()["error"]["code"] == "join.invited"
    token = approved["url"].rsplit("/", 1)[1]
    assert owner.get(f"/api/app/invitations/{token}").status_code == 200
    assert len(owner.get(f"{base}/invitations").json()["invitations"]) == 1
    assert bai.get(f"{base}/join-requests/mine").json()["suggestions"][0]["id"] == second


def test_failed_save_does_not_issue_an_invite_or_consume_a_suggestion(home, monkeypatch):
    owner, base = home["owner"], home["base"]
    member = _join(home)
    suggestion_id = _suggest(member, base, "visitor@example.com").json()["id"]
    approve = f"{base}/join-requests/{suggestion_id}/approve"

    def broken_save(self, state):
        raise OSError("simulated disk failure")

    with monkeypatch.context() as fault:
        fault.setattr(registry.MemberRegistry, "_save", broken_save)
        response = owner.post(approve, json={})
    assert response.status_code == 409 and response.json()["error"]["code"] == "unavailable"
    assert member.get(f"{base}/join-requests/mine").json()["suggestions"][0]["id"] == suggestion_id
    assert owner.get(f"{base}/invitations").json() == {"invitations": []}
    assert owner.post(approve, json={}).status_code == 200
