"""App archives preserve home data without carrying the external personal Vault."""

import io
from pathlib import Path
import tarfile

import pytest

from src.web.app_personal_vault import DIRECTORY, PersonalVault
from src.workflow.home_backup import backup
from src.workflow.home_identity import pairing_owner
from src.workflow.home_move import export_home
from tests.unit.test_assistant_backup import backup_mod


@pytest.fixture
def custody(tmp_path, monkeypatch):
    root = tmp_path / "home"
    state = root / ".vortoone"
    state.mkdir(parents=True)
    (state / "chat.json").write_text('{"test": "home chat"}')
    pairing_owner(root, legacy_confirmed=True)

    outside = tmp_path / "operator"
    outside.mkdir()
    vault = outside / DIRECTORY
    vault.mkdir(mode=0o700)
    master = vault / "master.key"
    master.write_bytes(bytes([7]) * 32)
    master.chmod(0o600)
    monkeypatch.setenv("VORTOONE_PERSONAL_VAULT_DIR", str(vault))
    store = PersonalVault(str(root), "archive-test-owner")
    item = store.create({"scope": "personal", "kind": "key", "title": "Archive test", "keyName": "TEST_KEY",
                         "secret": "synthetic archive test secret"})
    assert store.item(item["id"])["hasSecret"]
    assert store.path.is_file() and b"synthetic archive test secret" not in store.path.read_bytes()
    return root, state, vault, store.path.name


def _members(archive):
    with archive as tar:
        return set(tar.getnames())


def _assert_excluded(names, sealed_name):
    for name in names:
        assert DIRECTORY not in Path(name).parts
        assert Path(name).name not in {"master.key", sealed_name, "vault-alias", "vault-child.txt"}


def test_home_backup_and_move_leave_external_vault_and_its_alias_out(tmp_path, custody):
    root, state, vault, sealed_name = custody
    (state / "vault-alias").symlink_to(vault, target_is_directory=True)
    backed_up = backup(str(root), str(tmp_path / "backups"))["path"]
    move = tmp_path / "move.tar.gz"
    export_home(str(root), str(move))

    for package in (backed_up, move):
        names = _members(tarfile.open(package, "r:gz"))
        assert ".vortoone/chat.json" in names
        _assert_excluded(names, sealed_name)
    assert (vault / sealed_name).is_file()


def test_custom_assistant_sources_exclude_the_vault_parent_child_and_alias(tmp_path, monkeypatch, custody):
    _, _, vault, sealed_name = custody
    child = vault / "nested"
    child.mkdir(mode=0o700)
    (child / "vault-child.txt").write_text("synthetic nested vault fixture")
    public = vault.parent / "ordinary.txt"
    public.write_text("Keep the surrounding file.")
    alias = tmp_path / "vault-alias"
    alias.symlink_to(vault, target_is_directory=True)
    monkeypatch.setattr(backup_mod, "HOME", tmp_path)

    for source in (vault, child, alias, alias / "nested"):
        pack = backup_mod.archive([source])
        assert _members(tarfile.open(fileobj=io.BytesIO(pack), mode="r:gz")) == set()

    pack = backup_mod.archive([vault.parent, vault, child, alias, alias / "nested"])
    names = _members(tarfile.open(fileobj=io.BytesIO(pack), mode="r:gz"))
    assert names == {"operator/ordinary.txt"}
    _assert_excluded(names, sealed_name)
