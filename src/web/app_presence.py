"""Track authenticated Desktop connections for the people directory."""

from __future__ import annotations

from threading import Lock


_LOCK = Lock()
_SESSIONS: dict[object, tuple[str, str]] = {}


def connected(root: str, user: str) -> object:
    session = object()
    with _LOCK:
        _SESSIONS[session] = (root, user)
    return session


def disconnected(session: object) -> None:
    with _LOCK:
        _SESSIONS.pop(session, None)


def active_people(root: str) -> set[str]:
    with _LOCK:
        return {user for home, user in _SESSIONS.values() if home == root}
