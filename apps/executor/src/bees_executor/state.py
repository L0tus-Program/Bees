"""Exclusividade do estado privado durante toda a vida de uma CLI."""

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path


class StateLockError(RuntimeError):
    """Falha sanitizada ao adquirir o estado; não autoriza recuperação destrutiva."""


@contextmanager
def state_lock(directory: Path):
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(
            directory / "executor.lock",
            os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
            0o600,
        )
    except OSError:
        raise StateLockError("executor_state_unavailable") from None
    with os.fdopen(descriptor, "r+b") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise StateLockError("executor_state_unavailable")
        try:
            if os.name == "nt":
                import msvcrt

                # Windows permite travar além do EOF: não escrever antes de obter
                # exclusividade, nem truncar/substituir o arquivo compartilhado.
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            code = (
                "executor_state_in_use"
                if error.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK)
                else "executor_state_unavailable"
            )
            raise StateLockError(code) from None
        # Fechar o handle libera a trava, inclusive após erro; o SO também a
        # libera após crash. Não remover o arquivo: isso criaria duas travas.
        yield
