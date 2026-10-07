"""Arquivos TLS não podem depender de um ancestral substituível por outro usuário."""

import ctypes
import os
import stat
from pathlib import Path

from bees_host.guest_bridge.protocol import BridgeError


def check_ancestors(path: Path) -> None:
    if not path.is_absolute():
        raise BridgeError("bridge_identity_private_required")
    try:
        for parent in path.parents:
            info = parent.lstat()
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise BridgeError("bridge_identity_private_required")
            if os.name == "nt":
                _windows_parent(parent)
            elif info.st_uid not in {0, os.getuid()} or info.st_mode & 0o022:
                raise BridgeError("bridge_identity_private_required")
    except OSError:
        raise BridgeError("bridge_identity_private_required") from None


def _windows_parent(path: Path) -> None:
    """SYSTEM/Administrators fazem parte da confiança local; demais SIDs não escrevem."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr, dword = ctypes.c_void_p, ctypes.c_uint32
    kernel.GetCurrentProcess.restype = ptr
    kernel.CloseHandle.argtypes = [ptr]
    kernel.LocalFree.argtypes = [ptr]
    adv.OpenProcessToken.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
    adv.GetTokenInformation.argtypes = [ptr, ctypes.c_int, ptr, dword, ctypes.POINTER(dword)]
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(ptr)]
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
    adv.GetAce.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
    token, descriptor = ptr(), ptr()

    def sid_text(sid):
        output = ptr()
        try:
            if not adv.ConvertSidToStringSidW(sid, ctypes.byref(output)):
                raise BridgeError("bridge_identity_private_required")
            return ctypes.wstring_at(output)
        finally:
            if output:
                kernel.LocalFree(output)

    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise BridgeError("bridge_identity_private_required")
        needed = dword()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value < 65536:
            raise BridgeError("bridge_identity_private_required")
        user = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, user, needed, ctypes.byref(needed)):
            raise BridgeError("bridge_identity_private_required")
        trusted = {
            sid_text(ctypes.cast(user, ctypes.POINTER(ptr))[0]),
            "S-1-5-18",
            "S-1-5-32-544",
            "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
        }  # TrustedInstaller é dono comum da raiz Windows.
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
            or not acl
            or not owner
            or sid_text(owner) not in trusted
        ):
            raise BridgeError("bridge_identity_private_required")
        count = ctypes.c_uint16.from_address(acl.value + 4).value
        if count > 256:
            raise BridgeError("bridge_identity_private_required")
        # Criar irmãos não substitui o ancestral existente. DELETE_CHILD/DELETE,
        # WRITE_DAC/OWNER, atributos e GENERIC_WRITE/ALL permitem alterá-lo.
        write_mask = 0x500D0150
        for index in range(count):
            ace = ptr()
            if not adv.GetAce(acl, index, ctypes.byref(ace)):
                raise BridgeError("bridge_identity_private_required")
            kind = ctypes.c_ubyte.from_address(ace.value).value
            flags = ctypes.c_ubyte.from_address(ace.value + 1).value
            if flags & 8 or kind == 1:  # inherit-only / denied ACE cannot grant this parent.
                continue
            if kind != 0:
                raise BridgeError("bridge_identity_private_required")
            mask = ctypes.c_uint32.from_address(ace.value + 4).value
            permitted = trusted | {"S-1-3-4"}  # OWNER RIGHTS: dono já foi validado acima.
            if mask & write_mask and sid_text(ptr(ace.value + 8)) not in permitted:
                raise BridgeError("bridge_identity_private_required")
    finally:
        if token:
            kernel.CloseHandle(token)
        if descriptor:
            kernel.LocalFree(descriptor)
