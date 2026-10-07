import ctypes
import json
import os
from pathlib import Path

import pytest
from bees_host.guest_bridge.protocol import BridgeError
from bees_host.guest_bridge.tls import HostIdentity
from bees_host.security import check_private
from test_guest_bridge_tls import make_bridge_material, private_bridge_directory


def allow_everyone(path: Path):
    """Altera somente ACL/mode de fixture descartável, nunca Temp/profile existente."""
    if os.name != "nt":
        path.chmod(0o777 if path.is_dir() else 0o666)
        return
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr, dword = ctypes.c_void_p, ctypes.c_uint32
    kernel.LocalFree.argtypes = [ptr]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        ctypes.c_wchar_p,
        dword,
        ctypes.POINTER(ptr),
        ctypes.POINTER(dword),
    ]
    adv.GetSecurityDescriptorDacl.argtypes = [
        ptr,
        ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ptr),
        ctypes.POINTER(ctypes.c_int),
    ]
    adv.SetNamedSecurityInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_int, dword, ptr, ptr, ptr, ptr]
    descriptor = ptr()
    try:
        assert adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            "D:P(A;;FA;;;WD)", 1, ctypes.byref(descriptor), None
        )
        present, defaulted, acl = ctypes.c_int(), ctypes.c_int(), ptr()
        assert adv.GetSecurityDescriptorDacl(
            descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
        )
        assert not adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, acl, None)
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)


def test_private_leaf_under_replaceable_parent_is_rejected():
    with private_bridge_directory() as base:
        parent = base / "replaceable"
        parent.mkdir()
        check_private(parent, directory=True, protect=True)
        directory = parent / "identity"
        make_bridge_material(directory)
        allow_everyone(parent)
        with pytest.raises(BridgeError, match="^bridge_identity_private_required$"):
            HostIdentity.load(directory)


def test_unprotected_key_never_reaches_tls_context():
    with private_bridge_directory() as base:
        directory = base / "identity"
        make_bridge_material(directory)
        allow_everyone(directory / "server.key")
        with pytest.raises(BridgeError, match="bridge_identity") as caught:
            HostIdentity.load(directory)
        assert str(directory) not in str(caught.value)
        assert "PRIVATE KEY" not in str(caught.value)


def test_hard_linked_private_key_is_rejected():
    with private_bridge_directory() as base:
        directory = base / "identity"
        make_bridge_material(directory)
        os.link(directory / "server.key", directory / "duplicate.key")
        with pytest.raises(BridgeError, match="^bridge_identity_private_required$"):
            HostIdentity.load(directory)


@pytest.mark.parametrize(
    "changes",
    [
        {"format": True},
        {"generation": 2},
        {"guest_cert_sha256": "secret"},
        {"vsock_port": 2762},
        {"vsock_port": True},
        {"provider_key": "secret"},
    ],
)
def test_private_config_closed_and_certificate_cannot_be_rebound(changes):
    with private_bridge_directory() as base:
        directory = base / "identity"
        make_bridge_material(directory)
        path = directory / "identity.json"
        value = json.loads(path.read_bytes()) | changes
        path.write_text(json.dumps(value), encoding="utf-8")
        with pytest.raises(BridgeError) as caught:
            HostIdentity.load(directory)
        assert "secret" not in str(caught.value)
        assert str(directory) not in str(caught.value)


def test_certificate_role_and_pin_material_are_independent():
    with private_bridge_directory() as base:
        directory = base / "identity"
        make_bridge_material(directory)
        (directory / "server.crt").write_bytes((directory / "client.crt").read_bytes())
        with pytest.raises(BridgeError, match="^bridge_certificate_binding_invalid$"):
            HostIdentity.load(directory)


def test_key_size_is_bounded_before_loading_or_network():
    with private_bridge_directory() as base:
        directory = base / "identity"
        make_bridge_material(directory)
        (directory / "server.key").write_bytes(b"secret" * 11000)
        with pytest.raises(BridgeError, match="^bridge_identity_invalid$"):
            HostIdentity.load(directory)
