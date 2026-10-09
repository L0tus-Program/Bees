"""Executa pytest no runner Windows sem afrouxar guardas do produto.

O runner hospedado usa uma conta elevada cujo TokenOwner pode ser Administrators.
Somente o dono padrão do token do próprio processo muda, em RAM, durante a suíte.
Não muda usuário, grupos, privilégios, arquivos da instalação ou política do SO.
A prova negativa protege a DACL somente de uma fixture temporária própria.
Novos arquivos de prova e filhos Python/PowerShell precisam ter TokenUser como dono.
TokenOwner original é restaurado inclusive quando pytest falha ou lança exceção.
Isso adequa ownership das fixtures; não transforma o runner em conta não elevada.

Referência: https://learn.microsoft.com/windows/win32/secauthz/owner-of-a-new-object
"""

import base64
import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path


class WindowsOwner:
    """Handle limitado ao token primário deste processo, sem ajuste de privilégio."""

    def __init__(self):
        if os.name != "nt":
            raise RuntimeError("Este bootstrap de CI exige Windows.")
        self.adv = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        ptr, dword = ctypes.c_void_p, ctypes.c_uint32
        self.kernel.GetCurrentProcess.restype = ptr
        self.kernel.CloseHandle.argtypes = [ptr]
        self.kernel.LocalFree.argtypes = [ptr]
        self.kernel.LocalFree.restype = ptr
        self.adv.OpenProcessToken.argtypes = [ptr, dword, ctypes.POINTER(ptr)]
        self.adv.GetTokenInformation.argtypes = [
            ptr,
            ctypes.c_int,
            ptr,
            dword,
            ctypes.POINTER(dword),
        ]
        self.adv.SetTokenInformation.argtypes = [ptr, ctypes.c_int, ptr, dword]
        self.adv.EqualSid.argtypes = [ptr, ptr]
        self.adv.GetNamedSecurityInfoW.argtypes = [
            ctypes.c_wchar_p,
            ctypes.c_int,
            dword,
            ctypes.POINTER(ptr),
            ptr,
            ptr,
            ptr,
            ctypes.POINTER(ptr),
        ]
        self.adv.GetNamedSecurityInfoW.restype = dword
        self.token = ptr()
        self._require(
            self.adv.OpenProcessToken(
                self.kernel.GetCurrentProcess(), 0x0088, ctypes.byref(self.token)
            )
        )  # TOKEN_QUERY | TOKEN_ADJUST_DEFAULT.
        try:
            self.user = self._information(1)  # TOKEN_USER.
            self.original_owner = self._information(4)  # TOKEN_OWNER.
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _require(success):
        if not success:
            raise ctypes.WinError(ctypes.get_last_error())

    @staticmethod
    def _sid(buffer):
        return ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]

    def _information(self, kind):
        needed = ctypes.c_uint32()
        self.adv.GetTokenInformation(self.token, kind, None, 0, ctypes.byref(needed))
        if not 0 < needed.value < 65536:
            raise RuntimeError("Tamanho inválido de informação do token de CI.")
        buffer = ctypes.create_string_buffer(needed.value)
        self._require(
            self.adv.GetTokenInformation(
                self.token, kind, buffer, needed.value, ctypes.byref(needed)
            )
        )
        return buffer

    def owner_is_user(self):
        owner = self._information(4)
        return bool(self.adv.EqualSid(self._sid(owner), self._sid(self.user)))

    def _set_owner(self, buffer):
        owner = ctypes.c_void_p(self._sid(buffer))
        self._require(
            self.adv.SetTokenInformation(self.token, 4, ctypes.byref(owner), ctypes.sizeof(owner))
        )

    def use_current_user(self):
        self._set_owner(self.user)
        if not self.owner_is_user():
            raise RuntimeError("Dono padrão do token não corresponde à conta de CI.")

    def restore(self):
        self._set_owner(self.original_owner)
        owner = self._information(4)
        if not self.adv.EqualSid(self._sid(owner), self._sid(self.original_owner)):
            raise RuntimeError("Não foi possível restaurar o dono padrão do token de CI.")

    def assert_file_owner(self, path):
        owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
        result = self.adv.GetNamedSecurityInfoW(
            str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor)
        )  # SE_FILE_OBJECT, OWNER_SECURITY_INFORMATION.
        try:
            if result:
                raise ctypes.WinError(result)
            if not self.adv.EqualSid(owner, self._sid(self.user)):
                raise RuntimeError("Arquivo de prova da CI não pertence à conta atual.")
        finally:
            if descriptor:
                self.kernel.LocalFree(descriptor)

    def close(self):
        if self.token:
            self.kernel.CloseHandle(self.token)
            self.token = ctypes.c_void_p()


def _probe_children(owner, directory):
    created_directory = directory / "created-after-adjustment"
    created_directory.mkdir()
    owner.assert_file_owner(created_directory)
    own = directory / "parent.txt"
    own.write_bytes(b"ci-owner-probe")
    owner.assert_file_owner(own)
    child = directory / "python.txt"
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).write_bytes(b'ci')",
            str(child),
        ],
        check=True,
        timeout=30,
    )
    owner.assert_file_owner(child)
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    ps_file = directory / "powershell.txt"
    # Texto fixo; o caminho temporário é uma string literal PowerShell escapada.
    quoted = str(ps_file).replace("'", "''")
    script = f"$ErrorActionPreference='Stop'; [IO.File]::WriteAllText('{quoted}', 'ci')"
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(
        [str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        check=True,
        timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    owner.assert_file_owner(ps_file)


def main(arguments=None):
    arguments = sys.argv[1:] if arguments is None else arguments
    check_only = arguments == ["--check-owner"]
    owner = WindowsOwner()
    try:
        with tempfile.TemporaryDirectory(prefix="bees-ci-owner-") as temporary:
            directory = Path(temporary)
            # Criação anterior ao ajuste demonstra a negativa real no runner elevado.
            previous = directory / "previous-owner.txt"
            previous.write_bytes(b"ci-owner-negative")
            foreign_previous_owner = not owner.owner_is_user()
            try:
                owner.use_current_user()
                _probe_children(owner, directory)
                if foreign_previous_owner:
                    from bees_host.errors import HostError
                    from bees_host.security import check_private

                    try:
                        check_private(previous, protect=True)
                    except HostError as error:
                        if str(error) != "private_path_required":
                            raise
                    else:
                        raise RuntimeError(
                            "Guarda de produção aceitou fixture com dono estrangeiro."
                        )
                    print(
                        "CI: guarda recusou dono anterior estrangeiro; "
                        "owner padrão de fixtures validado."
                    )
                else:
                    print("CI: owner padrão já era a conta; novos arquivos e filhos validados.")
                if check_only:
                    return 0
                import pytest

                return pytest.main(arguments)
            finally:
                owner.restore()
    finally:
        owner.close()


if __name__ == "__main__":
    raise SystemExit(main())
