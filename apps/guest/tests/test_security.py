import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from tls_fixture import make_tls_fixture

from bees_guest import security
from bees_guest.errors import GuestError


@pytest.mark.parametrize(
    "changes",
    [
        {"st_uid": 10001},
        {"st_mode": stat.S_IFREG | 0o640},
        {"st_mode": stat.S_IFREG | 0o606},
        {"st_mode": stat.S_IFLNK | 0o600},
        {"st_mode": stat.S_IFIFO | 0o600},
        {"st_nlink": 2},
    ],
)
def test_private_target_rejects_owner_permissions_symlink_special_and_hardlink(
    monkeypatch, tmp_path, changes
):
    path = tmp_path / "client.key"
    regular = {"st_mode": stat.S_IFREG | 0o600, "st_uid": 0, "st_nlink": 1}
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_nlink=1)
    monkeypatch.setattr(security, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda current: SimpleNamespace(**(regular | changes)) if current == path else directory,
    )
    with pytest.raises(GuestError, match="private_path_required"):
        security.check_private(path)


@pytest.mark.parametrize("mode,owner", [(0o777, 0), (0o775, 0), (0o755, 10001)])
def test_private_ancestor_rejects_writeable_and_foreign_tree(monkeypatch, tmp_path, mode, owner):
    path = tmp_path / "client.key"
    monkeypatch.setattr(security, "os", SimpleNamespace(name="posix"))
    monkeypatch.setattr(
        Path,
        "lstat",
        lambda current: SimpleNamespace(
            st_mode=(stat.S_IFREG | 0o600) if current == path else stat.S_IFDIR | mode,
            st_uid=0 if current == path else owner,
            st_nlink=1,
        ),
    )
    with pytest.raises(GuestError, match="private_path_required"):
        security.check_private(path)


@pytest.mark.parametrize(
    "changed",
    [
        {"vsock_port": 2762},
        {"vsock_port": True},
        {"generation": True},
        {"format": True},
        {"guest_id": "00000000-0000-0000-0000-000000000000"},
        {"client_key": "/untrusted/key"},
        {"relay_cert_sha256": "private invalid value"},
    ],
)
def test_identity_is_closed_and_does_not_echo_configuration(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(security, "check_private", lambda *_args, **_kwargs: None)
    fixture = make_tls_fixture(tmp_path / "tls")
    path = fixture.path / "identity.json"
    document = json.loads(path.read_text()) | changed
    path.write_text(json.dumps(document))
    with pytest.raises(GuestError) as failure:
        security.load_identity(fixture.path)
    assert str(failure.value) == "identity_invalid"
    assert "private invalid value" not in repr(failure.value)
    assert repr(fixture.identity) == "<Identity private>"


def test_private_path_requires_absolute_and_posix(monkeypatch):
    monkeypatch.setattr(security, "os", SimpleNamespace(name="posix"))
    with pytest.raises(GuestError, match="private_path_required"):
        security.check_private(Path("client.key"))
    monkeypatch.setattr(security, "os", SimpleNamespace(name="nt"))
    with pytest.raises(GuestError, match="private_path_required"):
        security.check_private(Path.cwd())
