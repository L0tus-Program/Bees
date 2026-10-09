"""Armazenamento operacional de artefatos: staging fechado, publicação e versões.

Banco e arquivos não fecham numa transação única (ADR 0001). O rascunho grava antes o
hash e o tamanho pretendidos; a publicação é idempotente para o mesmo conteúdo e só
anuncia ``ready`` depois de o blob ser relido e conferido. Falha do ambiente mantém o
rascunho para repetição; somente conteúdo divergente no destino é terminal. A
reconciliação apenas relata: não apaga evidência nem conclui publicações.
"""

import csv
import hashlib
import io
import json
import os
import stat
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4, uuid5

from bees_core.models import Artifact
from bees_core.storage.database import Database
from bees_core.storage.store import IntegrityError, NotFoundError, StateStore

# Formatos produzidos por código confiável neste recorte; anexação externa é BEES-018.2.
MEDIA_TYPES = frozenset({"text/plain", "text/markdown", "text/csv", "application/json"})
DEFAULT_MAX_BYTES = 25 * 1024 * 1024
KEY_PREFIX = "blob/v1/"
_CHUNK = 1024 * 1024
# Antivírus/indexador podem segurar por instantes o staging recém-escrito no Windows.
_RENAME_ATTEMPTS = 5
_VERSION_NAMESPACE = UUID("2d6c1f1e-5a0b-4c55-9d0e-5f0f7a0e1801")
_CSV_LOCK = threading.Lock()
_IDENTITY = ("task_id", "run_id", "name", "media_type", "storage_key", "sha256", "size_bytes")
_LINEAGE = ("version", "series_id", "previous_id")


class ArtifactError(RuntimeError):
    """Erro de domínio seguro para API/CLI, sem caminho, conteúdo ou nome do usuário."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class _ContentMismatch(ArtifactError):
    """Arquivo estável no destino com tamanho ou hash diferentes do rascunho."""

    def __init__(self) -> None:
        super().__init__("artifact_integrity_failed")


@dataclass(frozen=True)
class ReconcileReport:
    """Somente identificadores; nenhuma correção é aplicada."""

    pending: list[UUID] = field(default_factory=list)
    interrupted: list[UUID] = field(default_factory=list)
    conflicting: list[UUID] = field(default_factory=list)
    integrity_failed: list[UUID] = field(default_factory=list)
    failed_with_blob: list[UUID] = field(default_factory=list)
    orphan_blobs: list[str] = field(default_factory=list)
    # Inclui stagings de publicadores ativos; relatar não implica abandono.
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
            # O limite global de campo do módulo csv (128 KiB) não é limite do formato.
            with _CSV_LOCK:
                previous = csv.field_size_limit(max(max_bytes, 1))
                try:
                    for _ in csv.reader(io.StringIO(text, newline=""), strict=True):
                        pass
                finally:
                    csv.field_size_limit(previous)
    except ValueError, csv.Error, RecursionError:
        raise ArtifactError("artifact_content_invalid") from None
    return hashlib.sha256(content).hexdigest(), len(content)


class ArtifactStore:
    def __init__(
        self,
        database: Database,
        root: Path,
        *,
        max_bytes: int = DEFAULT_MAX_BYTES,
        actor: str = "system",
    ) -> None:
        self.store = StateStore(database)
        # Não resolver: resolve esconderia symlinks/junctions nos ancestrais.
        self.root = Path(root).absolute()
        self.max_bytes = max_bytes
        self.actor = actor

    def _write(self):
        return self.store.transaction(actor=self.actor, source="artifacts")

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
            created = not self.root.exists()
            for path in (self.root, self._blobs, self._staging):
                path.mkdir(mode=0o700, parents=path == self.root, exist_ok=True)
                if os.name != "nt":
                    path.chmod(0o700)
            if created:
                # Diretórios novos sobrevivem a uma queda antes de o banco anunciar ready.
                self._sync_directory(self.root.parent)
                self._sync_directory(self.root)
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
        if os.name == "nt":
            for attempt in range(_RENAME_ATTEMPTS):
                try:
                    # No Windows, rename falha se o destino existe.
                    os.rename(staging, final)
                    break
                except FileExistsError:
                    return False
                except PermissionError as error:
                    if (
                        getattr(error, "winerror", None) not in (5, 32)
                        or attempt == _RENAME_ATTEMPTS - 1
                    ):
                        raise
                    time.sleep(0.05 * (attempt + 1))
        else:
            try:
                os.link(staging, final)
            except FileExistsError:
                return False
            # Antes dos fsyncs: o blob volta a ter um único link o quanto antes. Um
            # publicador concorrente do mesmo id pode já ter liberado este link.
            try:
                os.unlink(staging)
            except FileNotFoundError:
                pass
        self._sync_directory(self._blobs)
        self._sync_directory(self._staging)
        return True

    def _release_own_links(self, artifact: Artifact) -> None:
        """Remove links de staging do mesmo artefato que apontam para o blob publicado.

        Sobram de um publicador interrompido entre link e unlink (POSIX). Não guardam
        dado próprio: o conteúdo continua no blob. Stagings com outro inode ficam.
        """
        if os.name == "nt":
            return
        try:
            blob = self._blob_path(artifact).lstat()
            entries = list(os.scandir(self._staging))
        except FileNotFoundError:
            return
        prefix = f"{artifact.id}."
        for entry in entries:
            if not (entry.name.startswith(prefix) and entry.name.endswith(".part")):
                continue
            info = entry.stat(follow_symlinks=False)
            if (info.st_dev, info.st_ino) == (blob.st_dev, blob.st_ino):
                try:
                    os.unlink(entry.path)
                except FileNotFoundError:
                    pass

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
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino) or (
                opened.st_nlink != 1
            ):
                raise ArtifactError("artifact_integrity_failed")
            if opened.st_size != artifact.size_bytes:
                raise _ContentMismatch
            chunks = []
            total = 0
            digest = hashlib.sha256()
            while total <= artifact.size_bytes:
                chunk = os.read(descriptor, _CHUNK)
                if not chunk:
                    break
                chunks.append(chunk)
                digest.update(chunk)
                total += len(chunk)
        finally:
            os.close(descriptor)
        if total != artifact.size_bytes or digest.hexdigest() != artifact.sha256:
            raise _ContentMismatch
        return b"".join(chunks)

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
    def _same(existing: Artifact, expected: Artifact, names=_IDENTITY + _LINEAGE) -> bool:
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
            if expected.previous_id is None:
                raise ArtifactError("artifact_link_invalid") from None
        # O savepoint foi revertido; a transação segue utilizável para o diagnóstico.
        sibling = next(
            (
                item
                for item in uow.artifacts.list(series_id=expected.series_id, limit=1000)
                if item.version == expected.version and item.status != "failed"
            ),
            None,
        )
        if sibling is None:
            raise ArtifactError("artifact_link_invalid")
        # Rascunho na mesma posição exige repetição idêntica ou abandono explícito.
        raise ArtifactError(
            "artifact_version_pending" if sibling.status == "draft" else "artifact_version_conflict"
        )

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
        with self._write() as uow:
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
        with self.store.transaction(write=False) as uow:
            seen = self._owned(uow, agent_id, previous_id)
        # Validação fora da transação de escrita; a anterior é relida antes de gravar.
        media_type = media_type or seen.media_type
        name = display_name(name if name is not None else seen.name)
        digest, size = validate_content(media_type, content, self.max_bytes)
        # Id determinístico: repetir o mesmo pedido após crash readota o rascunho.
        artifact_id = artifact_id or uuid5(
            _VERSION_NAMESPACE, f"{seen.id}:{digest}:{media_type}:{name}:{run_id}"
        )
        with self._write() as uow:
            previous = self._owned(uow, agent_id, previous_id)
            if previous.status != "ready":
                raise ArtifactError("artifact_not_ready")
            if previous.revision != seen.revision:
                raise ArtifactError("artifact_version_conflict")
            expected = Artifact(
                id=artifact_id,
                task_id=previous.task_id,
                run_id=run_id,
                name=name,
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

    def abandon(
        self, agent_id: UUID | str, artifact_id: UUID | str, *, expected_revision: int
    ) -> Artifact:
        """Decisão explícita: encerra um rascunho sem tocar em arquivos ou evidência."""
        with self._write() as uow:
            draft = self._owned(uow, agent_id, artifact_id)
            if draft.status != "draft":
                raise ArtifactError("artifact_terminal")
            return uow.artifacts.update(
                draft.model_copy(
                    update={"status": "failed", "metadata": {"error_code": "artifact_abandoned"}}
                ),
                expected_revision,
            )

    def _fail(self, artifact: Artifact) -> None:
        try:
            with self._write() as uow:
                current = uow.artifacts.get(artifact.id)
                if current is not None and current.status == "draft":
                    uow.artifacts.update(
                        current.model_copy(
                            update={
                                "status": "failed",
                                "metadata": {"error_code": "artifact_integrity_failed"},
                            }
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
                # Outro publicador ou tentativa anterior chegou antes.
                self._release_own_links(draft)
            # Leitura independente do blob publicado antes de anunciar ready.
            self._read_verified(draft)
        except _ContentMismatch:
            # Somente conteúdo estável divergente no destino é terminal; evidência fica.
            self._fail(draft)
            raise ArtifactError("artifact_integrity_failed") from None
        except OSError:
            # Falha do ambiente: o rascunho permanece para repetição explícita.
            raise ArtifactError("artifact_storage_unavailable") from None
        finally:
            if staging is not None:
                try:
                    staging.unlink(missing_ok=True)
                except OSError:
                    pass
        with self._write() as uow:
            current = uow.artifacts.get(draft.id)
            # A confirmação exige exatamente o conteúdo e a linhagem que foram verificados.
            if current is None or not self._same(current, draft):
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
        try:
            return artifact, self._read_verified(artifact)
        except _ContentMismatch:
            raise ArtifactError("artifact_integrity_failed") from None

    def reconcile(self, *, page: int = 500) -> ReconcileReport:
        report = ReconcileReport()
        self._check_root()
        # Diretórios antes do banco: blob criado durante a varredura não vira órfão falso.
        listing: dict[Path, list[str]] = {}
        for directory in (self._blobs, self._staging):
            try:
                listing[directory] = sorted(entry.name for entry in os.scandir(directory))
            except FileNotFoundError:
                listing[directory] = []
            except OSError:
                raise ArtifactError("artifact_storage_unavailable") from None
        rows: list[Artifact] = []
        with self.store.transaction(write=False) as uow:
            # Uma única transação de leitura: as páginas vêm do mesmo snapshot.
            offset = 0
            while True:
                batch = uow.artifacts.list(limit=page, offset=offset)
                rows.extend(batch)
                if len(batch) < page:
                    break
                offset += page
        known: set[str] = set()
        for artifact in rows:
            if artifact.storage_key != KEY_PREFIX + str(artifact.id):
                continue
            known.add(str(artifact.id))
            try:
                (self._blobs / str(artifact.id)).lstat()
                present = True
            except FileNotFoundError:
                present = False
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
        report.orphan_blobs.extend(name for name in listing[self._blobs] if name not in known)
        report.staging_files.extend(listing[self._staging])
        return report
