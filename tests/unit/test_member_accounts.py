"""成员账号：密码、会话、重置链接与输错上锁。"""
from datetime import datetime, timedelta, timezone

import pytest

from src.workflow.members import accounts as module
from src.workflow.members.accounts import Locked, MemberAccounts, WrongCredentials

T0 = datetime(2026, 11, 16, tzinfo=timezone.utc)
PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def _fast_and_clean(monkeypatch):
    monkeypatch.setattr(module, "_SCRYPT", {"n": 2 ** 4, "r": 8, "p": 1})      # 测试里不花真实的算力
    monkeypatch.setattr(module, "_DECOY", module._sealed("decoy-password"))
    module._FAILURES.clear()
    module._RECENT.update(since=0.0, count=0)
    yield
    module._FAILURES.clear()
    module._RECENT.update(since=0.0, count=0)


@pytest.fixture
def accounts(tmp_path):
    return MemberAccounts(str(tmp_path))


def test_nothing_usable_is_written_to_disk(accounts):
    account = accounts.create(" Ana@Example.com ", "小王", PASSWORD)
    _, session = accounts.sign_in("ana@example.com", PASSWORD)
    reset, _ = accounts.reset_link(account)
    stored = (accounts.directory / "accounts.json").read_text(encoding="utf-8")
    assert PASSWORD not in stored and session not in stored and reset not in stored
    assert "ana@example.com" in stored and "Ana@Example.com" not in stored
    assert (accounts.directory / "accounts.json").stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("email, name, password", [
    ("not-an-email", "小王", PASSWORD), ("a@b.c", "", PASSWORD), ("a@b.c", "小王", "short"),
    ("a@b.c", "小王", "x" * 129), ("a@b.c", PASSWORD, PASSWORD), ("a@b.c", "小\n王", PASSWORD)])
def test_bad_sign_ups_are_refused(accounts, email, name, password):
    with pytest.raises(ValueError):
        accounts.create(email, name, password)
    assert not (accounts.directory / "accounts.json").exists()


def test_one_account_per_email(accounts):
    accounts.create("a@b.c", "小王", PASSWORD)
    with pytest.raises(ValueError):
        accounts.create("A@B.C", "小李", PASSWORD)


def test_sign_in_and_session_lifetime(accounts):
    account = accounts.create("a@b.c", "小王", PASSWORD)
    got, token = accounts.sign_in("A@b.c", PASSWORD, now=T0)
    assert got == account
    assert accounts.session(token, now=T0) == {"id": account, "email": "a@b.c", "name": "小王", "member": ""}
    assert accounts.session(token, now=T0 + timedelta(days=30)) is None
    assert accounts.session("not-a-session", now=T0) is None and accounts.session("", now=T0) is None
    accounts.sign_out(token)
    assert accounts.session(token, now=T0) is None


@pytest.mark.parametrize("email, password", [("a@b.c", "wrong password"), ("nobody@b.c", PASSWORD),
                                             ("not-an-email", PASSWORD), ("a@b.c", ""), ("a@b.c", None)])
def test_wrong_email_and_wrong_password_look_the_same(accounts, email, password):
    accounts.create("a@b.c", "小王", PASSWORD)
    with pytest.raises(WrongCredentials) as caught:
        accounts.sign_in(email, password)
    assert str(caught.value) == "邮箱或密码不对"


def test_repeated_failures_lock_that_email_for_a_while(accounts):
    accounts.create("a@b.c", "小王", PASSWORD)
    for attempt in range(module.LOCK_AFTER):
        with pytest.raises(WrongCredentials):
            accounts.sign_in("a@b.c", "wrong password", clock=100.0 + attempt)
    with pytest.raises(Locked):                        # 锁住后连对的密码也不行
        accounts.sign_in("a@b.c", PASSWORD, clock=110.0)
    accounts.create("other@b.c", "小李", PASSWORD)
    assert accounts.sign_in("other@b.c", PASSWORD, clock=110.0)          # 别的邮箱不受影响
    assert accounts.sign_in("a@b.c", PASSWORD, clock=104.0 + module.LOCK_SECONDS)


def test_a_success_clears_the_count(accounts):
    accounts.create("a@b.c", "小王", PASSWORD)
    for round_ in range(3):
        for attempt in range(module.LOCK_AFTER - 1):
            with pytest.raises(WrongCredentials):
                accounts.sign_in("a@b.c", "wrong password", clock=float(round_ * 10 + attempt))
        assert accounts.sign_in("a@b.c", PASSWORD, clock=float(round_ * 10 + 9))


def test_the_whole_service_stops_checking_under_a_flood(accounts, monkeypatch):
    monkeypatch.setattr(module, "ALL_FAILURES_PER_MINUTE", 3)
    accounts.create("a@b.c", "小王", PASSWORD)
    for index in range(3):
        with pytest.raises(WrongCredentials):
            accounts.sign_in(f"x{index}@b.c", "wrong password", clock=10.0)
    with pytest.raises(Locked):
        accounts.sign_in("a@b.c", PASSWORD, clock=20.0)
    assert accounts.sign_in("a@b.c", PASSWORD, clock=71.0)


def test_too_many_devices_sign_the_oldest_out(accounts, monkeypatch):
    monkeypatch.setattr(module, "MAX_SESSIONS_EACH", 2)
    account = accounts.create("a@b.c", "小王", PASSWORD)
    first = accounts.open_session(account, now=T0)
    second = accounts.open_session(account, now=T0 + timedelta(minutes=1))
    third = accounts.open_session(account, now=T0 + timedelta(minutes=2))
    at = T0 + timedelta(minutes=3)
    assert accounts.session(first, now=at) is None
    assert accounts.session(second, now=at) and accounts.session(third, now=at)


def test_reset_link_works_once_and_signs_everything_out(accounts):
    account = accounts.create("a@b.c", "小王", PASSWORD)
    _, session = accounts.sign_in("a@b.c", PASSWORD, now=T0)
    stale, _ = accounts.reset_link(account, now=T0)
    token, expires = accounts.reset_link(account, now=T0)                # 新开的顶掉旧的
    assert expires == "2026-11-17T00:00:00+00:00"
    with pytest.raises(LookupError):
        accounts.reset_password(stale, "a brand new password", now=T0)
    with pytest.raises(ValueError):
        accounts.reset_password(token, "short", now=T0)
    with pytest.raises(LookupError):
        accounts.reset_password(token, "a brand new password", now=T0 + timedelta(hours=24))
    assert accounts.reset_password(token, "a brand new password", now=T0) == account
    assert accounts.session(session, now=T0) is None
    with pytest.raises(WrongCredentials):
        accounts.sign_in("a@b.c", PASSWORD)
    assert accounts.sign_in("a@b.c", "a brand new password")
    with pytest.raises(LookupError):
        accounts.reset_password(token, "yet another password", now=T0)
    with pytest.raises(LookupError):
        accounts.reset_password("", "yet another password", now=T0)


def test_removing_the_member_removes_the_account_and_its_sessions(accounts):
    account = accounts.create("a@b.c", "小王", PASSWORD)
    other = accounts.create("o@b.c", "小李", PASSWORD)
    accounts.bind_member(account, "m_1")
    session, kept = accounts.open_session(account, now=T0), accounts.open_session(other, now=T0)
    assert accounts.account_of("m_1") == account and accounts.account_of("") is None
    accounts.remove_member("")                          # 空的成员 id 不能误删还没加入的账号
    assert accounts.get(other) is not None
    accounts.remove_member("m_1")
    assert accounts.get(account) is None and accounts.session(session, now=T0) is None
    assert accounts.session(kept, now=T0) is not None
    with pytest.raises(WrongCredentials):
        accounts.sign_in("a@b.c", PASSWORD)


def test_a_damaged_file_means_nobody_is_signed_in(accounts):
    account = accounts.create("a@b.c", "小王", PASSWORD)
    session = accounts.open_session(account, now=T0)
    (accounts.directory / "accounts.json").write_text("{", encoding="utf-8")
    assert accounts.session(session, now=T0) is None
    with pytest.raises(ValueError):
        accounts.sign_in("a@b.c", PASSWORD)
    with pytest.raises(ValueError):
        accounts.create("n@b.c", "小李", PASSWORD)


def test_replacing_password_uses_a_recent_explicit_sign_in_and_keeps_only_that_session(accounts):
    account = accounts.create("a@b.c", "小王", PASSWORD)
    other = accounts.create("o@b.c", "小李", PASSWORD)
    activation = accounts.open_session(account, now=T0)
    _, current = accounts.sign_in("a@b.c", PASSWORD, now=T0)
    _, sibling = accounts.sign_in("a@b.c", PASSWORD, now=T0)
    _, unaffected = accounts.sign_in("o@b.c", PASSWORD, now=T0)
    proposed = "a brand new password"
    with pytest.raises(module.ReauthenticationRequired):
        accounts.change_password(account, activation, proposed, now=T0)
    with pytest.raises(module.ReauthenticationRequired):
        accounts.change_password(account, unaffected, proposed, now=T0)
    with pytest.raises(module.ReauthenticationRequired):
        accounts.change_password(account, current, proposed, now=T0 + timedelta(minutes=10))
    with pytest.raises(ValueError, match="不同"):
        accounts.change_password(account, current, PASSWORD, now=T0)
    with pytest.raises(ValueError, match="密码要"):
        accounts.change_password(account, current, "short", now=T0)

    reset, _ = accounts.reset_link(account, now=T0)
    now = T0 + timedelta(minutes=9, seconds=59)
    accounts.change_password(account, current, proposed, now=now)
    assert accounts.session(current, now=now)["id"] == account
    assert accounts.session(activation, now=now) is None
    assert accounts.session(sibling, now=now) is None
    assert accounts.session(unaffected, now=now)["id"] == other
    assert accounts.get(account)["email"] == "a@b.c"
    with pytest.raises(LookupError):
        accounts.reset_password(reset, "password from old link", now=now)
    with pytest.raises(WrongCredentials):
        accounts.sign_in("a@b.c", PASSWORD, now=now)

    _, reconnected = accounts.sign_in("a@b.c", proposed, now=now)
    accounts.change_password(account, current, proposed, now=now)
    assert accounts.session(reconnected, now=now)["id"] == account


@pytest.mark.parametrize("operation", ["replacement", "reset"])
def test_an_inflight_old_password_cannot_issue_a_session_after_revocation(accounts, monkeypatch, operation):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, current_thread

    account = accounts.create("a@b.c", "小王", PASSWORD)
    _, current = accounts.sign_in("a@b.c", PASSWORD, now=T0)
    reset, _ = accounts.reset_link(account, now=T0)
    computed, release = Event(), Event()
    original = module._matches

    def wait_after_verification(sealed, password):
        valid = original(sealed, password)
        if current_thread().name.startswith("old-password"):
            assert valid and password == PASSWORD
            computed.set()
            assert release.wait(timeout=5)
        return valid

    monkeypatch.setattr(module, "_matches", wait_after_verification)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="old-password") as executor:
        attempt = executor.submit(accounts.sign_in, "a@b.c", PASSWORD, now=T0)
        try:
            assert computed.wait(timeout=5)
            if operation == "replacement":
                accounts.change_password(account, current, "a brand new password", now=T0)
            else:
                accounts.reset_password(reset, "a brand new password", now=T0)
                with pytest.raises(module.ReauthenticationRequired):
                    accounts.change_password(account, current, "other proposed password", now=T0)
        finally:
            release.set()
        with pytest.raises(WrongCredentials):
            attempt.result(timeout=5)
    assert accounts.sign_in("a@b.c", "a brand new password", now=T0)[0] == account


def test_revocation_io_failure_leaves_member_password_and_sessions_retryable(accounts, monkeypatch):
    from src.workflow.ai_apps.store import AiApps
    from src.workflow.members.one_time import PENDING

    account = accounts.create("failure@b.c", "小王", PASSWORD)
    accounts.bind_member(account, "m_failure")
    _, current = accounts.sign_in("failure@b.c", PASSWORD, now=T0)
    _, sibling = accounts.sign_in("failure@b.c", PASSWORD, now=T0)
    link, _ = PENDING.mint("account:" + account)

    def unavailable(*args):
        raise OSError("injected storage failure")

    with monkeypatch.context() as mocked:
        mocked.setattr(AiApps, "disconnect_person", unavailable)
        with pytest.raises(OSError, match="injected"):
            accounts.change_password(account, current, "a brand new password", now=T0)
    assert accounts.session(current, now=T0)["id"] == account
    assert accounts.session(sibling, now=T0)["id"] == account
    assert accounts.sign_in("failure@b.c", PASSWORD, now=T0)[0] == account
    accounts.change_password(account, current, "a brand new password", now=T0)
    assert accounts.session(sibling, now=T0) is None
    assert PENDING.redeem(link) is None


def test_a_link_in_another_worker_cannot_outlive_a_password_change(accounts, monkeypatch):
    from src.workflow.members import one_time

    account = accounts.create("a@b.c", "小王", PASSWORD)
    _, current = accounts.sign_in("a@b.c", PASSWORD)
    minting_worker = one_time.OneTimeSignIns()
    changing_worker = one_time.OneTimeSignIns()
    monkeypatch.setattr(one_time, "PENDING", minting_worker)
    link, _ = accounts.mint_one_time(current, account)
    monkeypatch.setattr(one_time, "PENDING", changing_worker)
    accounts.change_password(account, current, "a brand new password")

    monkeypatch.setattr(one_time, "PENDING", minting_worker)
    with pytest.raises(LookupError, match="已失效"):
        accounts.redeem_one_time(link)
    assert accounts.redeem_one_time(link) == (None, None, {})
    new_link, _ = accounts.mint_one_time(current, account)
    _, redeemed, profile = accounts.redeem_one_time(new_link)
    assert redeemed and profile["id"] == account


def test_activated_account_is_not_published_when_session_creation_fails(accounts, monkeypatch):
    def unavailable(*args):
        raise OSError("injected activation failure")

    with monkeypatch.context() as mocked:
        mocked.setattr(MemberAccounts, "_issue_session", unavailable)
        with pytest.raises(OSError, match="injected"):
            accounts.create_with_activation_session("a@b.c", "小王", PASSWORD)
    assert not accounts.exists("a@b.c")
    account, activation = accounts.create_with_activation_session("a@b.c", "小王", PASSWORD)
    assert accounts.session(activation)["id"] == account
    with pytest.raises(module.ReauthenticationRequired):
        accounts.change_password(account, activation, "a brand new password")
