"""A task invitation is tied to the current owner and exact device connection."""
import asyncio
from types import SimpleNamespace

from src.gateway import paired_browser_task
from src.gateway.computer_browser_preview import browser_previews
from src.gateway.computer_connections import connections


async def test_replaced_connection_expired_and_consumed_codes_stay_invalid(tmp_path, monkeypatch):
    clock = [100]
    monkeypatch.setattr(paired_browser_task, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(connections, "_schedule", lambda *args, **kwargs: None)
    identity = ("a" * 32, "b" * 32, "c" * 32)
    fields = dict(zip(("home_instance_id", "paired_principal_id", "trust_epoch"), identity))
    fields["connector_id"] = "d" * 32
    root = str(tmp_path)
    task = paired_browser_task.PairedBrowserTask(root, "https://example.test", browser_previews.owner_epoch(), identity)
    pending = asyncio.get_running_loop().create_future()
    task.start(pending)
    original = connections.add(root, fields, None)
    replacement = None
    try:
        first = task.invite(original)
        assert first is not None
        replacement = connections.add(root, fields, None)
        assert paired_browser_task.redeem_browser_invite(first, replacement) is None
        assert paired_browser_task.redeem_browser_invite(first, original) is None
        replaced = task.invite(replacement)
        assert replaced is not None
        clock[0] += paired_browser_task.INVITE_SECONDS
        assert paired_browser_task.redeem_browser_invite(replaced, replacement) is None
        usable = task.invite(replacement)
        assert usable is not None
        assert paired_browser_task.redeem_browser_invite(usable, replacement) is task
        assert paired_browser_task.redeem_browser_invite(usable, replacement) is None
        remaining = task.invite(replacement)
        assert remaining is not None
        pending.set_result(False)
        assert paired_browser_task.redeem_browser_invite(remaining, replacement) is None
    finally:
        task.close()
        connections.remove(original)
        if replacement is not None:
            connections.remove(replacement)
