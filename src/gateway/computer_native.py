"""Single-use local-launch ticket, received only on the launcher's private stdin.

This is not an owner/mobile credential, IPC server or authorization for tools.
No ticket is persisted or written to a response, environment or log.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
from typing import Callable

from src.gateway.computer_protocol import ComputerProtocolError, decode_frame
from src.gateway.connected_computers import ConnectedComputers, ComputerDenied, ComputerUnavailable

TICKET_SECONDS = 60
BOOTSTRAP_READ_SECONDS = 5
_TICKET = re.compile(r"vcn_[a-f0-9]{64}\Z")
_lock = threading.Lock()


class ComputerNativeDenied(PermissionError):
    pass


class ComputerNativeUnclaimed(PermissionError):
    pass


class ComputerNativeUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class _Bootstrap:
    root: str
    home_instance_id: str
    trust_epoch: str
    digest: bytes
    parent_pid: int
    created: float
    clock: Callable[[], float]


_pending: _Bootstrap | None = None


def register_bootstrap(root, ticket: str, parent_pid: int, *, clock=time.monotonic) -> None:
    """Trusted process initialization only. HTTP inputs must never call this function."""
    global _pending
    if (not isinstance(ticket, str) or not _TICKET.fullmatch(ticket)
            or type(parent_pid) is not int or parent_pid <= 1 or parent_pid != os.getppid()):
        raise ComputerNativeDenied("invalid native launch")
    created = clock()
    try:
        identity = ConnectedComputers(root).identity()
    except (ComputerUnavailable, OSError):
        raise ComputerNativeUnavailable("native launch unavailable") from None
    with _lock:
        if _pending is not None:
            raise ComputerNativeDenied("native launch already initialized")
        _pending = _Bootstrap(str(Path(root).resolve()), identity["home_instance_id"], identity["trust_epoch"],
                              hashlib.sha256(ticket.encode("ascii")).digest(),
                              parent_pid, created, clock)


def read_bootstrap(root) -> None:
    """Read one bounded frame before the native-managed server starts listening."""
    result: queue.Queue = queue.Queue(maxsize=1)
    descriptor = sys.stdin.fileno()
    def read():
        raw = bytearray()
        try:
            while len(raw) <= 1024:
                part = os.read(descriptor, 1)
                if not part:
                    break
                raw.extend(part)
                if part == b"\n":
                    break
            result.put_nowait(bytes(raw))
        except OSError:
            result.put_nowait(b"")
    # os.read holds no BufferedReader lock at interpreter shutdown.
    threading.Thread(target=read, daemon=True).start()
    try:
        raw = result.get(timeout=BOOTSTRAP_READ_SECONDS)
        if len(raw) > 1024 or not raw.endswith(b"\n"):
            raise ComputerNativeDenied("invalid native launch")
        frame = decode_frame(raw)
        if set(frame) != {"ticket", "parent_pid"}:
            raise ComputerNativeDenied("invalid native launch")
        register_bootstrap(root, frame["ticket"], frame["parent_pid"])
    except (queue.Empty, ComputerProtocolError, KeyError):
        raise ComputerNativeDenied("invalid native launch") from None


def redeem(root, ticket: str, connector_id: str, name: str) -> dict:
    """Redeem exactly once, including when ownership/storage then refuses the grant."""
    global _pending
    if not isinstance(ticket, str) or not _TICKET.fullmatch(ticket):
        raise ComputerNativeDenied("native launch unavailable")
    supplied = hashlib.sha256(ticket.encode("ascii")).digest()
    with _lock:
        pending = _pending
        if pending is None:
            raise ComputerNativeDenied("native launch unavailable")
        now = pending.clock()
        if (not hmac.compare_digest(supplied, pending.digest) or pending.root != str(Path(root).resolve())
                or pending.parent_pid != os.getppid() or not pending.created <= now < pending.created + TICKET_SECONDS):
            raise ComputerNativeDenied("native launch unavailable")
        _pending = None
    try:
        identity = ConnectedComputers(root).identity()
        if (identity["home_instance_id"] != pending.home_instance_id or identity["trust_epoch"] != pending.trust_epoch):
            raise ComputerNativeDenied("native launch unavailable")
        return ConnectedComputers(root).pair_native(connector_id, name, expected_identity={
            "home_instance_id": pending.home_instance_id, "trust_epoch": pending.trust_epoch})
    except ComputerNativeDenied:
        raise
    except ComputerDenied:
        raise ComputerNativeUnclaimed("native pairing unavailable") from None
    except (ComputerUnavailable, OSError, ValueError, KeyError):
        raise ComputerNativeUnavailable("native pairing unavailable") from None
