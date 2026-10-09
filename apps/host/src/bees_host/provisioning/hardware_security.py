"""Guarda readonly da área VMMS: não concede ACL, eleva ou ativa Hyper-V."""

import ctypes
import os
import stat
from pathlib import Path

from bees_host.errors import HostError
from bees_host.guest_bridge.private import check_ancestors
from bees_host.guest_bridge.protocol import BridgeError
from bees_host.provisioning.contracts import ProvisionError
from bees_host.security import check_private


def _windows_acl(path: Path):
    """Dono atual; DACL protected com três ACEs allow/FA, sem SID de VM na raiz."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr, dword = ctypes.c_void_p, ctypes.c_uint32
    kernel.GetCurrentProcess.restype = ptr
    kernel.CloseHandle.argtypes = [ptr]
    kernel.LocalFree.argtypes = [ptr]
    adv.OpenProcessToken.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
    adv.GetTokenInformation.argtypes = [ptr, ctypes.c_int, ptr, dword, ctypes.POINTER(dword)]
    adv.GetNamedSecurityInfoW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_int,
        dword,
        ctypes.POINTER(ptr),
        ptr,
        ctypes.POINTER(ptr),
        ptr,
        ctypes.POINTER(ptr),
    ]
    adv.GetSecurityDescriptorControl.argtypes = [
        ptr,
        ctypes.POINTER(ctypes.c_uint16),
        ctypes.POINTER(dword),
    ]
    adv.GetAce.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
    adv.EqualSid.argtypes = [ptr, ptr]
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(ptr)]
    token, descriptor = ptr(), ptr()

    def sid_text(sid):
        output = ptr()
        try:
            if not adv.ConvertSidToStringSidW(sid, ctypes.byref(output)):
                raise ValueError("invalid")
            return ctypes.wstring_at(output)
        finally:
            if output:
                kernel.LocalFree(output)

    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise ValueError("invalid")
        needed = dword()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value < 65536:
            raise ValueError("invalid")
        user = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, user, needed, ctypes.byref(needed)):
            raise ValueError("invalid")
        sid = ctypes.cast(user, ctypes.POINTER(ptr))[0]
        expected = {sid_text(sid), "S-1-5-18", "S-1-5-32-544"}
        owner, acl = ptr(), ptr()
        if (
            adv.GetNamedSecurityInfoW(
                str(path),
                1,
                5,
                ctypes.byref(owner),
                None,
                ctypes.byref(acl),
                None,
                ctypes.byref(descriptor),
            )
            or not owner
            or not acl
            or not adv.EqualSid(owner, sid)
        ):
            raise ValueError("invalid")
        control, revision = ctypes.c_uint16(), dword()
        if (
            not adv.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            )
            or not control.value & 0x1000
        ):
            raise ValueError("invalid")
        count = ctypes.c_uint16.from_address(acl.value + 4).value
        if count != 3:
            raise ValueError("invalid")
        observed = set()
        for index in range(count):
            ace = ptr()
            if not adv.GetAce(acl, index, ctypes.byref(ace)):
                raise ValueError("invalid")
            kind = ctypes.c_ubyte.from_address(ace.value).value
            flags = ctypes.c_ubyte.from_address(ace.value + 1).value
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            identity = sid_text(ptr(ace.value + 8))
            if kind != 0 or flags not in {0, 3} or mask != 0x1F01FF or identity in observed:
                raise ValueError("invalid")
            observed.add(identity)
        if observed != expected:
            raise ValueError("invalid")
    finally:
        if token:
            kernel.CloseHandle(token)
        if descriptor:
            kernel.LocalFree(descriptor)


def _validate(path: Path, *, directory: bool):
    try:
        if not isinstance(path, Path) or not path.is_absolute():
            raise ValueError("invalid")
        if os.name == "nt":
            if len(path.drive) != 2 or path.drive[1] != ":":
                raise ValueError("invalid")
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetDriveTypeW.argtypes = [ctypes.c_wchar_p]
            if kernel.GetDriveTypeW(path.anchor) != 3:
                raise ValueError("invalid")
        if any(getattr(parent.lstat(), "st_file_attributes", 0) & 0x400 for parent in path.parents):
            raise ValueError("invalid")
        check_ancestors(path)
        info = path.lstat()
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & 0x400
            or not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode))
            or not directory
            and info.st_nlink != 1
        ):
            raise ValueError("invalid")
        if os.name == "nt":
            _windows_acl(path)
        else:
            check_private(path, directory=directory)
    except OSError, ValueError, TypeError, HostError, BridgeError:
        raise ProvisionError("provision_hardware_private_required") from None


def validate_hardware_root(path: Path):
    """Somente verifica a raiz existente CurrentUser/SYSTEM/Administrators."""
    _validate(path, directory=True)


def validate_hardware_file(path: Path):
    """Marcador/lock do workspace: mesmo vínculo e sem hardlink/reparse."""
    _validate(path, directory=False)
