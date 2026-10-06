"""Proteção nativa do usuário atual; sem dependência do banco ou cofre do serviço."""

import ctypes
import os
import stat
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken

from bees_host.errors import HostError


class Cipher(Protocol):
    def encrypt(self, value: bytes) -> bytes: ...

    def decrypt(self, value: bytes) -> bytes: ...


class _Blob(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


class DPAPICipher:
    """CurrentUser com UI_FORBIDDEN; falha sem alternativa plaintext."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise HostError("credential_protection_unavailable")
        self.crypt = ctypes.WinDLL("crypt32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        for name in ("CryptProtectData", "CryptUnprotectData"):
            method = getattr(self.crypt, name)
            method.argtypes = [
                ctypes.POINTER(_Blob),
                ctypes.c_void_p,
                ctypes.POINTER(_Blob),
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint32,
                ctypes.POINTER(_Blob),
            ]
            method.restype = ctypes.c_int
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def _transform(self, value: bytes, decrypt: bool) -> bytes:
        buffer = ctypes.create_string_buffer(value)
        source = _Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
        result = _Blob()
        method = self.crypt.CryptUnprotectData if decrypt else self.crypt.CryptProtectData
        try:
            if not method(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
                raise HostError("credential_protection_unavailable")
            if not result.data or not 0 < result.length <= 65536:
                raise HostError("credential_protection_unavailable")
            return ctypes.string_at(result.data, result.length)
        finally:
            ctypes.memset(buffer, 0, len(buffer))
            if result.data:
                ctypes.memset(result.data, 0, result.length)
                self.kernel.LocalFree(result.data)

    def encrypt(self, value: bytes) -> bytes:
        return self._transform(value, False)

    def decrypt(self, value: bytes) -> bytes:
        return self._transform(value, True)


class FernetCipher:
    """Chave externa explícita: não gera chave junto da credencial."""

    def __init__(self, key: str) -> None:
        try:
            self.cipher = Fernet(key.encode("ascii"))
        except ValueError, UnicodeError:
            raise HostError("credential_protection_unavailable") from None

    def encrypt(self, value: bytes) -> bytes:
        return self.cipher.encrypt(value)

    def decrypt(self, value: bytes) -> bytes:
        try:
            return self.cipher.decrypt(value)
        except InvalidToken:
            raise HostError("credential_protection_unavailable") from None


def native_cipher() -> Cipher:
    if os.name == "nt":
        return DPAPICipher()
    key = os.environ.get("BEES_HOST_STATE_KEY")
    if not key:
        raise HostError("credential_protection_unavailable")
    return FernetCipher(key)


def _windows_security(path: Path, *, protect: bool) -> None:
    """Exige dono atual e DACL protegida sem ACE que conceda a outra identidade."""
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    ptr = ctypes.c_void_p
    dword = ctypes.c_uint32
    kernel.GetCurrentProcess.restype = ptr
    kernel.CloseHandle.argtypes = [ptr]
    kernel.LocalFree.argtypes = [ptr]
    kernel.LocalFree.restype = ptr
    adv.OpenProcessToken.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
    adv.GetTokenInformation.argtypes = [ptr, ctypes.c_int, ptr, dword, ctypes.POINTER(dword)]
    adv.ConvertSidToStringSidW.argtypes = [ptr, ctypes.POINTER(ptr)]
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
    token = ptr()
    descriptor = ptr()
    text = ptr()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise HostError("private_path_required")
        needed = dword()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
        if not 0 < needed.value < 65536:
            raise HostError("private_path_required")
        user = ctypes.create_string_buffer(needed.value)
        if not adv.GetTokenInformation(token, 1, user, needed, ctypes.byref(needed)):
            raise HostError("private_path_required")
        sid = ctypes.cast(user, ctypes.POINTER(ptr))[0]
        if protect:
            if not adv.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise HostError("private_path_required")
            sddl = "D:P(A;;FA;;;" + ctypes.wstring_at(text) + ")"
            if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl, 1, ctypes.byref(descriptor), None
            ):
                raise HostError("private_path_required")
            present, defaulted, acl = ctypes.c_int(), ctypes.c_int(), ptr()
            if not adv.GetSecurityDescriptorDacl(
                descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(defaulted)
            ) or adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, acl, None):
                raise HostError("private_path_required")
            kernel.LocalFree(descriptor)
            descriptor = ptr()
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
            or not adv.EqualSid(owner, sid)
            or not acl
        ):
            raise HostError("private_path_required")
        control, version = ctypes.c_uint16(), dword()
        if (
            not adv.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(version)
            )
            or not control.value & 0x1000
        ):
            raise HostError("private_path_required")
        # ACL header: revision/reserved/size/ace_count/reserved, 8 bytes.
        count = ctypes.c_uint16.from_address(acl.value + 4).value
        if not 1 <= count <= 64:
            raise HostError("private_path_required")
        for index in range(count):
            ace = ptr()
            if not adv.GetAce(acl, index, ctypes.byref(ace)):
                raise HostError("private_path_required")
            # ACCESS_ALLOWED_ACE: 4-byte header, DWORD mask, SID begins at byte 8.
            if ctypes.c_ubyte.from_address(ace.value).value != 0 or not adv.EqualSid(
                ptr(ace.value + 8), sid
            ):
                raise HostError("private_path_required")
    finally:
        if token:
            kernel.CloseHandle(token)
        if descriptor:
            kernel.LocalFree(descriptor)
        if text:
            kernel.LocalFree(text)


def check_no_links(path: Path) -> None:
    for ancestor in [*reversed(path.parents), path]:
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise HostError("private_path_required")


def check_private(path: Path, *, directory: bool = False, protect: bool = False) -> None:
    check_no_links(path)
    info = path.lstat()
    if (directory and not stat.S_ISDIR(info.st_mode)) or (
        not directory and not stat.S_ISREG(info.st_mode)
    ):
        raise HostError("private_path_required")
    if os.name == "nt":
        _windows_security(path, protect=protect)
    else:
        if info.st_uid != os.getuid():
            raise HostError("private_path_required")
        if protect:
            path.chmod(0o700 if directory else 0o600)
        elif stat.S_IMODE(info.st_mode) & 0o077:
            raise HostError("private_path_required")
