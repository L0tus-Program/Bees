"""Armazenamento operacional de artefatos: staging fechado, publicação e versões.

Banco e arquivos não fecham numa transação única (ADR 0001). O rascunho grava antes o
hash e o tamanho pretendidos; a publicação é idempotente para o mesmo conteúdo e só
anuncia ``ready`` depois de o blob ser relido e conferido. Reconciliação apenas relata:
não apaga evidência, não reaproveita staging alheio e não conclui publicações.
"""

import csv
import hashlib
import io
import json
import os
import stat
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from bees_core.models import Artifact
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, NotFoundError, StateStore

# Formatos produzidos por código confiável neste recorte; anexação externa é BEES-018.2.
MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json"})
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
KEY_PREFIX = "blob/v1/"
_CHUNK = 1024 * 1024


class ArtifactError(RuntimeError):
    """Erro de domínio seguro para API/CLI, sem caminho, conteúdo ou nome do usuário."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ReconcileReport:
    """Somente identificadores; nenhuma correção é aplicada."""

    pending: list[UUID] = field(default_factory=list)
    interrupted: list[UUID] = field(default_factory=list)
    conflicting: list[UUID] = field(default_factory=list)
    integrity_failed: list[UUID] = field(default_factory=list)
    failed_with_blob: list[UUID] = field(default_factory=list)
    orphan_blobs: list[str] = field(default_factory=list)
    staging_files: list[str] = field(default_factory=list)


def display_name(value: str) -> str:
    """Nome é só exibição; nunca vira caminho e não carrega controles ou separadores."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 200
        or value != value.strip()
        or value in (".", "..")
        or any(char in "/\\" for char in value)
        or any(unicodedata.category(char) in ("Cc", "Cf", "Zl", "Zp") for char in value)
    ):
        raise ArtifactError("artifact_name_invalid")
    return value


def _pairs(values):
    result = {}
    for key, value in values:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def _constant(value):
    raise ValueError


def validate_content(media_type: str, content: bytes, max_bytes: int) -> tuple[str, int]:
    if media_type not in MEDIA_TYPES:
        raise ArtifactError("artifact_media_type_unsupported")
    if not isinstance(content, bytes) or len(content) > max_bytes:
        raise ArtifactError("artifact_too_large")
    try:
        text = content.decode("utf-8", errors="strict")
        if "\x00" in text:
            raise ValueError
        if media_type == "application/json":
            json.loads(text, object_pairs_hook=_pairs, parse_constant=_constant)
        elif media_type == "text/csv":
            for _ in csv.reader(io.StringIO(text, newline=""), strict=True):
                pass
    except ValueError, csv.Error, RecursionError:
        raise ArtifactError("artifact_content_invalid") from None
    return hashlib.sha256(content).hexdigest(), len(content)


class ArtifactStore:
    def __init__(
        self, database: Database, root: Path, *, max_bytes: int = DEFAULT_MAX_BYTES
    ) -> None:
        self.store = StateStore(database)
        # Não resolver: resolve esconderia symlinks/junctions nos ancestrais.
        self.root = Path(root).absolute()
        self.max_bytes = max_bytes

    # Sistema de arquivos -------------------------------------------------------------

    @staticmethod
    def _is_link(info: os.stat_result) -> bool:
        return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)

    def _check_root(self) -> None:
        for path in [*reversed(self.root.parents), self.root, self._blobs, self._staging]:
            try:
                info = path.lstat()
            except FileNotFoundError:
                continue
            except OSError:
                raise ArtifactError("artifact_storage_unavailable") from None
            if self._is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise ArtifactError("artifact_storage_unavailable")

    @property
    def _blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def _staging(self) -> Path:
        return self.root / "staging"

    def _prepare(self) -> None:
        self._check_root()
        try:
            for path in (self.root, self._blobs, self._staging):
                path.mkdir(mode=0o700, parents=path == self.root, exist_ok=True)
                if os.name != "nt":
                    path.chmod(0o700)
        except OSError:
            raise ArtifactError("artifact_storage_unavailable") from None
        self._check_root()

    @staticmethod
    def _sync_directory(path: Path) -> None:
        if os.name == "nt":
            return
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _blob_path(self, artifact: Artifact) -> Path:
        # A chave vem do banco, mas o caminho deriva só do UUID canônico.
        if artifact.storage_key != KEY_PREFIX + str(artifact.id):
            raise ArtifactError("artifact_integrity_failed")
        return self._blobs / str(artifact.id)

    def _write_staging(self, artifact_id: UUID, content: bytes) -> Path:
        path = self._staging / f"{artifact_id}.{uuid4().hex}.part"
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        descriptor = os.open(path, flags, 0o600)
        try:
            try:
                view = memoryview(content)
                while view:
                    written = os.write(descriptor, view[:_CHUNK])
                    view = view[written:]
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except BaseException:
            # Parcial próprio desta tentativa; nunca remove staging de outro publicador.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return path

    def _place(self, staging: Path, final: Path) -> bool:
        """Publicação sem sobrescrita; False quando outro publicador chegou antes."""
        try:
            if os.name == "nt":
                # No Windows, rename falha se o destino existe.
                os.rename(staging, final)
            else:
                # link não sobrescreve; o staging próprio é removido pelo chamador.
                os.link(staging, final)
        except FileExistsError:
            return False
        self._sync_directory(self._blobs)
        self._sync_directory(self._staging)
        return True

    def _read_verified(self, artifact: Artifact) -> bytes:
        path = self._blob_path(artifact)
        self._check_root()
        try:
            info = path.lstat()
        except FileNotFoundError:
            raise ArtifactError("artifact_blob_missing") from None
        except OSError:
            raise ArtifactError("artifact_storage_unavailable") from None
        if self._is_link(info) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ArtifactError("artifact_integrity_failed")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            raise ArtifactError("artifact_integrity_failed") from None
        try:
            opened = os.fstat(descriptor)
            if (
                (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_nlink != 1
                or opened.st_size != artifact.size_bytes
            ):
                raise ArtifactError("artifact_integrity_failed")
            data = bytearray()
            while len(data) <= artifact.size_bytes:
                chunk = os.read(descriptor, _CHUNK)
                if not chunk:
                    break
                data.extend(chunk)
        finally:
            os.close(descriptor)
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
            raise ArtifactError("artifact_integrity_failed")
        return bytes(data)

    # Domínio -------------------------------------------------------------------------

    @staticmethod
    def _owned(uow, agent_id: UUID | str, artifact_id: UUID | str) -> Artifact:
        artifact = uow.artifacts.get(artifact_id)
        task = uow.tasks.get(artifact.task_id) if artifact is not None else None
        if artifact is None or task is None or task.agent_id != UUID(str(agent_id)):
            raise NotFoundError("Artefato não encontrado.")
        return artifact

    @staticmethod
    def _task(uow, agent_id: UUID | str, task_id: UUID | str):
        task = uow.tasks.get(task_id)
        if task is None or task.agent_id != UUID(str(agent_id)):
            raise NotFoundError("Tarefa não encontrada.")
        return task

    @staticmethod
    def _same(existing: Artifact, expected: Artifact) -> bool:
        names = (
            "task_id",
            "run_id",
            "name",
            "media_type",
            "storage_key",
            "sha256",
            "size_bytes",
            "version",
            "series_id",
            "previous_id",
        )
        return all(getattr(existing, name) == getattr(expected, name) for name in names)

    def _draft(self, uow, expected: Artifact) -> Artifact:
        existing = uow.artifacts.get(expected.id)
        if existing is not None:
            # Replay do mesmo id só continua quando conteúdo e vínculos são idênticos.
            if not self._same(existing, expected):
                raise ArtifactError("artifact_conflict")
            return existing
        try:
            return uow.artifacts.create(expected)
        except IntegrityError:
            raise ArtifactError("artifact_version_conflict") from None

    def create(
        self,
        agent_id: UUID | str,
        task_id: UUID | str,
        *,
        name: str,
        media_type: str,
        content: bytes,
        run_id: UUID | None = None,
        artifact_id: UUID | None = None,
    ) -> Artifact:
        digest, size = validate_content(media_type, content, self.max_bytes)
        artifact_id = artifact_id or uuid4()
        expected = Artifact(
            id=artifact_id,
            task_id=UUID(str(task_id)),
            run_id=run_id,
            name=display_name(name),
            media_type=media_type,
            storage_key=KEY_PREFIX + str(artifact_id),
            sha256=digest,
            size_bytes=size,
            series_id=artifact_id,
        )
        with self.store.transaction(source="artifacts") as uow:
            self._task(uow, agent_id, task_id)
            draft = self._draft(uow, expected)
        return self._publish(draft, content)

    def create_version(
        self,
        agent_id: UUID | str,
        previous_id: UUID | str,
        *,
        content: bytes,
        name: str | None = None,
        media_type: str | None = None,
        run_id: UUID | None = None,
        artifact_id: UUID | None = None,
    ) -> Artifact:
        artifact_id = artifact_id or uuid4()
        with self.store.transaction(write=False) as uow:
            seen = self._owned(uow, agent_id, previous_id)
        # Validação fora da transação de escrita; a anterior é relida antes de gravar.
        media_type = media_type or seen.media_type
        digest, size = validate_content(media_type, content, self.max_bytes)
        with self.store.transaction(source="artifacts") as uow:
            previous = self._owned(uow, agent_id, previous_id)
            if previous.status != "ready":
                raise ArtifactError("artifact_not_ready")
            if previous.revision != seen.revision:
                raise ArtifactError("artifact_version_conflict")
            expected = Artifact(
                id=artifact_id,
                task_id=previous.task_id,
                run_id=run_id,
                name=display_name(name if name is not None else previous.name),
                media_type=media_type,
                storage_key=KEY_PREFIX + str(artifact_id),
                sha256=digest,
                size_bytes=size,
                version=previous.version + 1,
                # Registros anteriores à linhagem iniciam a série pelo próprio id.
                series_id=previous.series_id or previous.id,
                previous_id=previous.id,
            )
            # UNIQUE(series_id,version) garante um único vencedor entre versões concorrentes.
            draft = self._draft(uow, expected)
        return self._publish(draft, content)

    def _fail(self, artifact: Artifact, code: str) -> None:
        try:
            with self.store.transaction(source="artifacts") as uow:
                current = uow.artifacts.get(artifact.id)
                if current is not None and current.status == "draft":
                    uow.artifacts.update(
                        current.model_copy(
                            update={"status": "failed", "metadata": {"error_code": code}}
                        ),
                        current.revision,
                    )
        except Exception:
            # Sem confirmação o rascunho permanece pendente e a reconciliação o relata.
            pass

    def _publish(self, draft: Artifact, content: bytes) -> Artifact:
        if hashlib.sha256(content).hexdigest() != draft.sha256 or len(content) != draft.size_bytes:
            raise ArtifactError("artifact_conflict")
        if draft.status == "ready":
            self._read_verified(draft)
            return draft
        if draft.status != "draft":
            raise ArtifactError("artifact_terminal")
        self._prepare()
        final = self._blob_path(draft)
        staging = None
        try:
            staging = self._write_staging(draft.id, content)
            if not self._place(staging, final):
                # Outro publicador ou tentativa anterior: aceitar só o mesmo conteúdo.
                self._read_verified(draft)
        except ArtifactError as error:
            if error.code == "artifact_integrity_failed":
                self._fail(draft, error.code)
            raise
        except OSError:
            self._fail(draft, "artifact_storage_unavailable")
            raise ArtifactError("artifact_storage_unavailable") from None
        finally:
            if staging is not None:
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass
        # Leitura independente do blob publicado antes de anunciar ready.
        self._read_verified(draft)
        with self.store.transaction(source="artifacts") as uow:
            current = uow.artifacts.get(draft.id)
            if current is None:
                raise ArtifactError("artifact_conflict")
            if current.status == "ready":
                return current
            if current.status != "draft":
                raise ArtifactError("artifact_terminal")
            return uow.artifacts.update(
                current.model_copy(update={"status": "ready"}), current.revision
            )

    def list(
        self, agent_id: UUID | str, task_id: UUID | str, *, limit: int = 100, offset: int = 0
    ) -> list[Artifact]:
        with self.store.transaction(write=False) as uow:
            self._task(uow, agent_id, task_id)
            return uow.artifacts.list(task_id=UUID(str(task_id)), limit=limit, offset=offset)

    def read(self, agent_id: UUID | str, artifact_id: UUID | str) -> tuple[Artifact, bytes]:
        with self.store.transaction(write=False) as uow:
            artifact = self._owned(uow, agent_id, artifact_id)
        if artifact.status != "ready":
            raise ArtifactError("artifact_not_ready")
        return artifact, self._read_verified(artifact)

    def reconcile(self, *, page: int = 500) -> ReconcileReport:
        report = ReconcileReport()
        self._check_root()
        known: set[str] = set()
        offset = 0
        while True:
            with self.store.transaction(write=False) as uow:
                rows = uow.artifacts.list(limit=page, offset=offset)
            for artifact in rows:
                if artifact.storage_key != KEY_PREFIX + str(artifact.id):
                    continue
                known.add(str(artifact.id))
                present = (self._blobs / str(artifact.id)).exists()
                if artifact.status == "draft":
                    if not present:
                        report.pending.append(artifact.id)
                        continue
                    try:
                        self._read_verified(artifact)
                    except ArtifactError:
                        report.conflicting.append(artifact.id)
                    else:
                        report.interrupted.append(artifact.id)
                elif artifact.status == "ready":
                    try:
                        self._read_verified(artifact)
                    except ArtifactError:
                        report.integrity_failed.append(artifact.id)
                elif artifact.status == "failed" and present:
                    report.failed_with_blob.append(artifact.id)
            if len(rows) < page:
                break
            offset += page
        for directory, target in (
            (self._blobs, report.orphan_blobs),
            (self._staging, report.staging_files),
        ):
            try:
                names = sorted(entry.name for entry in os.scandir(directory))
            except FileNotFoundError:
                names = []
            except OSError:
                raise ArtifactError("artifact_storage_unavailable") from None
            target.extend(name for name in names if directory == self._staging or name not in known)
        return report
