"""Canal IPC privado do único adaptador de processo; sem retry/fallback."""

import os
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from bees_core.models import Action

from .authority import CoreToolAuthority
from .contracts import (
    ERROR_CODES,
    MAX_SECONDS,
    ResourceIdentity,
    TransportError,
    canonical_uuid,
    decode,
    digest,
    envelope,
    exact,
    read_frame,
    result,
    write_frame,
)
from .journal import Journal, private


@dataclass(frozen=True)
class ProcessTextResource:
    directory: Path
    identity: ResourceIdentity

    @classmethod
    def initialize(cls, directory: Path, installation_id: str, agent_id: str):
        identity = ResourceIdentity(str(installation_id), str(uuid4()), str(agent_id))
        journal = Journal.initialize(directory, identity)
        journal.close()
        return cls(Path(directory).absolute(), identity)

    @classmethod
    def open(cls, directory: Path):
        directory = Path(directory).absolute()
        private(directory, directory=True)
        private(directory / "identity.json")
        try:
            with (directory / "identity.json").open("rb") as source:
                data = source.read(2049)
            if len(data) > 2048:
                raise ValueError()
            value = decode(data)
            identity = ResourceIdentity(
                value["installation_id"], value["resource_id"], value["agent_id"]
            )
        except OSError, ValueError, KeyError, TypeError:
            raise TransportError("executor_journal_invalid") from None
        # Validar sem recuperar started: somente o processo dono abre o journal.
        return cls(directory, identity)


class ExecutorTransport(Protocol):
    def execute(self, authority: CoreToolAuthority) -> Action: ...

    def inspect(self, request: dict) -> dict: ...


class ProcessTextTransport:
    """Processo POSIX próprio executa exclusivamente text.normalize@1.

    A implementação não aceita comando, módulo, endpoint, env ou credencial do
    chamador. Não é executora de código arbitrário nem isolamento de filesystem.
    """

    def __init__(self, resource: ProcessTextResource):
        if os.name != "posix":
            raise TransportError("executor_platform_unavailable")
        self.resource = resource

    @staticmethod
    def _command(descriptor):
        source = str(Path(__file__).resolve().parents[2])
        code = (
            f"import sys;sys.path.insert(0,{source!r});"
            "from bees_core.executor_transport.runtime import main;main()"
        )
        return [sys.executable, "-I", "-S", "-B", "-c", code, str(descriptor)]

    @contextmanager
    def _session(self):
        private(self.resource.directory, directory=True)
        parent, child = socket.socketpair()
        process = None
        deadline = time.monotonic() + MAX_SECONDS
        try:
            process = subprocess.Popen(
                self._command(child.fileno()),
                pass_fds=(child.fileno(),),
                cwd=self.resource.directory,
                env={},
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                shell=False,
            )
            child.close()
            credential = "bx_" + secrets.token_urlsafe(32)
            write_frame(
                parent,
                {
                    "protocol": 1,
                    "type": "bootstrap",
                    "credential": credential,
                    **self.resource.identity.fields(),
                },
                deadline,
            )
            ready = self._receive(parent, deadline)
            exact(ready, {"protocol", "type", "resource_id", "pid", "capabilities"})
            if (
                type(ready["protocol"]) is not int
                or ready["protocol"] != 1
                or ready["type"] != "ready"
                or ready["pid"] != process.pid
                or type(ready["pid"]) is not int
                or ready["resource_id"] != self.resource.identity.resource_id
                or ready["capabilities"] != ["pure_text"]
            ):
                raise TransportError("executor_protocol_invalid")
            yield parent, credential, deadline
        except OSError:
            raise TransportError("executor_unavailable") from None
        finally:
            parent.close()
            child.close()
            if process is not None:
                try:
                    process.wait(timeout=0.2)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=1)

    @staticmethod
    def _receive(stream, deadline):
        value = read_frame(stream, deadline)
        if value.get("type") == "error":
            exact(value, {"protocol", "type", "code"})
            code = value["code"]
            if (
                type(value["protocol"]) is not int
                or value["protocol"] != 1
                or type(code) is not str
                or code not in ERROR_CODES
            ):
                raise TransportError("executor_protocol_invalid")
            raise TransportError(code)
        return value

    @staticmethod
    def _state(value, request):
        exact(
            value,
            {
                "protocol",
                "type",
                "action_id",
                "request_id",
                "request_hash",
                "status",
                "result",
                "cached",
            },
        )
        if (
            type(value["protocol"]) is not int
            or value["protocol"] != 1
            or value["type"] != "state"
            or value["action_id"] != request["action_id"]
            or value["request_id"] != request["request_id"]
            or value["request_hash"] != digest(request)
            or type(value["cached"]) is not bool
            or value["status"]
            not in ("missing", "intent", "started", "receipt", "unknown", "stopped")
        ):
            raise TransportError("executor_protocol_invalid")
        if value["status"] == "receipt":
            result(value["result"])
        elif value["result"] is not None:
            raise TransportError("executor_protocol_invalid")
        return value

    def inspect(self, request: dict) -> dict:
        """Consulta privada autenticada. Não envia GO nem publica no core."""
        envelope(request, self.resource.identity)
        with self._session() as (stream, credential, deadline):
            write_frame(
                stream, {"type": "inspect", "credential": credential, "envelope": request}, deadline
            )
            return self._state(self._receive(stream, deadline), request)

    def execute(self, authority: CoreToolAuthority) -> Action:
        if not isinstance(authority, CoreToolAuthority):
            raise TransportError("executor_authority_required")
        if authority.identity != self.resource.identity:
            raise TransportError("executor_binding_mismatch")
        action, request = authority.seal()
        if request is None:
            return action
        begun = False
        try:
            with self._session() as (stream, credential, deadline):
                write_frame(
                    stream,
                    {
                        "type": "invoke",
                        "credential": credential,
                        "envelope": request,
                        "arguments": authority.arguments,
                    },
                    deadline,
                )
                prepared = self._receive(stream, deadline)
                if prepared.get("type") == "state":
                    self._state(prepared, request)
                    raise TransportError("executor_request_replay")
                exact(
                    prepared,
                    {"protocol", "type", "action_id", "request_id", "request_hash", "nonce"},
                )
                canonical_uuid(prepared["nonce"])
                if (
                    type(prepared["protocol"]) is not int
                    or prepared["protocol"] != 1
                    or prepared["type"] != "prepared"
                    or prepared["action_id"] != request["action_id"]
                    or prepared["request_id"] != request["request_id"]
                    or prepared["request_hash"] != digest(request)
                ):
                    raise TransportError("executor_protocol_invalid")
                permit = {
                    "protocol": 1,
                    "type": "go",
                    "credential": credential,
                    "nonce": prepared["nonce"],
                    "request_hash": digest(request),
                }
                try:
                    authority.begin(request)
                except Exception:
                    try:
                        write_frame(stream, permit | {"type": "stop"}, deadline)
                        self._state(self._receive(stream, deadline), request)
                    except TransportError:
                        pass
                    raise
                begun = True
                write_frame(stream, permit, deadline)
                observed = self._state(self._receive(stream, deadline), request)
                if observed["status"] != "receipt" or observed["cached"]:
                    raise TransportError("executor_result_invalid")
                return authority.complete(request, observed["result"])
        except Exception:
            if begun:
                return authority.unknown(request)
            raise
