"""Fresh installations cannot promote their generated owner key into computer pairing."""
import pytest

from src.runtime_bootstrap import prepare_server_ownership
from src.utils.state_dir import state_dir
from src.workflow.home_identity import ensure_home_identity, pairing_owner


def test_fresh_server_requires_claim_even_after_explicit_legacy_confirmation(tmp_path):
    prepare_server_ownership(tmp_path)
    original = ensure_home_identity(tmp_path)
    assert original['ownership_state'] == 'unclaimed'
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path, legacy_confirmed=True)
    prepare_server_ownership(tmp_path)
    assert ensure_home_identity(tmp_path) == original


def test_existing_home_still_needs_explicit_verified_legacy_migration(tmp_path):
    state_dir(tmp_path).mkdir(mode=0o700)
    prepare_server_ownership(tmp_path)
    with pytest.raises(PermissionError):
        pairing_owner(tmp_path)
    owner = pairing_owner(tmp_path, legacy_confirmed=True)
    assert owner['ownership_state'] == 'claimed'
    prepare_server_ownership(tmp_path)
    assert ensure_home_identity(tmp_path) == owner
