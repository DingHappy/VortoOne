"""Ephemeral authorization for a verified owner input and its one-use confirmation."""
from __future__ import annotations

from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import time
from typing import TYPE_CHECKING
import uuid

if TYPE_CHECKING:
    from src.gateway.task_browser import TaskBrowserGrant


@dataclass(frozen=True)
class OwnerTurn:
    root: str
    session: str
    rid: str
    file_identity: tuple[str, str, str] | None = None
    task_browser: TaskBrowserGrant | None = None
    paired_browser_epoch: object | None = None

    def matches(self, identity: dict) -> bool:
        return (identity.get("ownership_state") == "claimed" and
                self.file_identity == (
                    identity.get("home_instance_id"), identity.get("owner_subject"), identity.get("trust_epoch")))


current_owner_turn: ContextVar[OwnerTurn | None] = ContextVar("owner_turn", default=None)
current_file_confirmation: ContextVar[OwnerTurn | None] = ContextVar("computer_file_confirmation", default=None)
_queued: OrderedDict[str, tuple[OwnerTurn, str, float]] = OrderedDict()


def bind_prompt(owner: OwnerTurn, item: dict) -> str:
    # The public request ID can come from other clients. Only the server mints this distinct queue ID.
    prompt_id = "owner-" + uuid.uuid4().hex
    item["id"] = prompt_id
    _queued[prompt_id] = (owner, item["text"], time.monotonic() + 900)
    if len(_queued) > 256:
        _queued.popitem(last=False)
    return prompt_id


def discard_prompt(prompt_id: str) -> None:
    _queued.pop(prompt_id, None)


@contextmanager
def owner_prompt(root: str, session: str, item: dict):
    record = _queued.get(item["id"])
    owner = None
    if record is not None:
        candidate, text, expires = record
        if ((candidate.root, candidate.session, candidate.rid, text) ==
                (root, session, item.get("rid"), item["text"])):
            discard_prompt(item["id"])
            if expires > time.monotonic():
                owner = candidate
    token = current_owner_turn.set(owner)
    try:
        yield
    finally:
        current_owner_turn.reset(token)


@contextmanager
def file_confirmation(owner: OwnerTurn):
    token = current_file_confirmation.set(owner)
    try:
        yield
    finally:
        current_file_confirmation.reset(token)
