"""Desktop's recycle bin moves existing artifact directories with their version history."""
from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import shutil
from typing import Literal

from src.agents.tools.artifacts import ArtifactStore


_RETENTION = timedelta(days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


class TrashConflict(ValueError):
    """The active and deleted files with the same ID must not overwrite one another."""


class ArtifactTrash(ArtifactStore):
    """Private state under `artifact-trash/<id>/<unix timestamp>/`, or `desktop-artifact-trash/` for the
    owner's uploads: the two stores stay apart in the recycle bin too, so restore lands where it came from.

    Moving the complete active directory to `payload` commits a delete. A purge moves it to
    `discard` before removing the bytes. An empty timestamp directory records the deletion
    so stale undo attempts get the same response after cleanup or a restart.
    """

    @property
    def _trash_root(self) -> Path:
        path = self.root.with_name("artifact-trash" if self.root.name == "artifacts" else "desktop-artifact-trash")
        if path.is_symlink():
            raise OSError("回收站目录不能是符号链接")
        return path

    def _entry(self, artifact_id: str) -> tuple[Path, datetime] | None:
        bucket = self._trash_root / artifact_id
        if bucket.is_symlink():
            raise OSError("回收站条目不能是符号链接")
        if not bucket.exists():
            return None
        entries = list(bucket.iterdir())
        if not entries:
            return None
        entry = entries[0]
        if len(entries) != 1 or re.fullmatch(r"[0-9]{1,10}", entry.name) is None:
            raise OSError("回收站条目损坏")
        if entry.is_symlink() or not entry.is_dir():
            raise OSError("回收站条目不是目录")
        return entry, datetime.fromtimestamp(int(entry.name), timezone.utc)

    @staticmethod
    def _payload(entry: Path) -> Path:
        payload = entry / "payload"
        marker = payload / "meta.json"
        if payload.is_symlink() or marker.is_symlink():
            raise OSError("回收站文件无效")
        return payload

    def _discard(self, entry: Path, *, remove_payload: bool) -> bool:
        payload = self._payload(entry)
        pending = entry / "discard"
        if pending.is_symlink():
            raise OSError("永久删除目录无效")
        # A previous physical delete may have been interrupted after the logical purge.
        removed = pending.exists()
        if removed:
            shutil.rmtree(pending)
        if remove_payload and payload.exists():
            payload.rename(pending)
            shutil.rmtree(pending)
            return True
        return removed

    def move_to_trash(self, artifact_id: str) -> bool:
        aid = self._safe_id(artifact_id)
        if not aid or not self.root.is_dir():
            return False
        with self._mutation():
            source = self._dir(aid)
            if source.is_symlink() or (source / "meta.json").is_symlink():
                raise OSError("制品目录无效")
            if (self.meta(aid) or {}).get("id") != aid:
                return False
            now = _now()
            old = self._entry(aid)
            if old is not None:
                entry, deleted = old
                self._discard(entry, remove_payload=now >= deleted + _RETENTION)
                if self._payload(entry).exists():
                    raise TrashConflict("回收站还有同编号的制品，请先永久删除该旧版")
                entry.rmdir()
            target = self._trash_root / aid / str(int(now.timestamp()))
            target.mkdir(parents=True, mode=0o700)
            try:
                source.rename(target / "payload")
            except OSError:
                target.rmdir()
                raise
            return True

    def trashed(self) -> list[tuple[dict, datetime, datetime]]:
        if not self._trash_root.is_dir():
            return []
        rows = []
        with self._mutation():
            now = _now()
            for bucket in self._trash_root.iterdir():
                if self._safe_id(bucket.name) != bucket.name:
                    continue
                stored = self._entry(bucket.name)
                if stored is None:
                    continue
                entry, deleted = stored
                purge_at = deleted + _RETENTION
                self._discard(entry, remove_payload=now >= purge_at)
                meta = self._read_meta(self._payload(entry))
                if meta and meta.get("id") == bucket.name:
                    rows.append((meta, deleted, purge_at))
        return sorted(rows, key=lambda row: (row[1], row[0]["id"]), reverse=True)

    def restore(self, artifact_id: str) -> Literal["not_found", "expired", "purged", "restored"]:
        aid = self._safe_id(artifact_id)
        if not aid or not self._trash_root.is_dir():
            return "not_found"
        with self._mutation():
            stored = self._entry(aid)
            if stored is None:
                return "not_found"
            entry, deleted = stored
            payload = self._payload(entry)
            active = self._dir(aid)
            expired = _now() >= deleted + _RETENTION
            if not payload.exists():
                # A process may have stopped after making the timestamp directory but before the move.
                if active.exists():
                    return "not_found"
                return "expired" if expired else "purged"
            if expired:
                return "expired"
            if (self._read_meta(payload) or {}).get("id") != aid:
                raise OSError("回收站制品没有有效元数据")
            if active.exists() or active.is_symlink():
                raise TrashConflict("当前 Drive 已有同编号的制品，回收站内容仍保留")
            payload.rename(active)
            # The move committed the restore. An empty timestamp directory is harmless.
            with suppress(OSError):
                entry.rmdir()
            return "restored"

    def purge(self, artifact_id: str) -> bool:
        aid = self._safe_id(artifact_id)
        if not aid or not self._trash_root.is_dir():
            return False
        with self._mutation():
            stored = self._entry(aid)
            return stored is not None and self._discard(stored[0], remove_payload=True)
