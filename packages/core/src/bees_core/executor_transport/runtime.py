"""Filho descartável de texto: stdlib, IPC privado, sem bridge bp_/bh_ ou HTTP.

O recurso POSIX é próprio do operador. Este processo não é uma sandbox de SO;
nunca carregar plugins/código fornecido por modelo neste adaptador fixo.
"""

import hmac
import os
import socket
import sys
import time
from uuid import uuid4

from .contracts import (
    CREDENTIAL,
    MAX_SECONDS,
    ResourceIdentity,
    TransportError,
    arguments,
    current_deadline,
    digest,
    envelope,
    exact,
    read_frame,
    result,
    write_frame,
)
from .journal import Journal


def normalize(value):
    text = value["text"]
    if value["operation"] == "trim":
        text = text.strip()
    elif value["operation"] == "upper":
        text = text.upper()
    else:
        text = text.lower()
    return result({"text": text})


def authenticate(value, credential):
    supplied = value.get("credential")
    if (
        type(supplied) is not str
        or CREDENTIAL.fullmatch(supplied) is None
        or not hmac.compare_digest(supplied, credential)
    ):
        raise TransportError("executor_credentials_invalid")


def state(request, status, output=None, *, cached):
    return {
        "protocol": 1,
        "type": "state",
        "action_id": request["action_id"],
        "request_id": request["request_id"],
        "request_hash": digest(request),
        "status": status,
        "result": output,
        "cached": cached,
    }


def serve(stream):
    deadline = time.monotonic() + MAX_SECONDS
    journal = None
    request = None
    started = False
    try:
        bootstrap = read_frame(stream, deadline)
        exact(
            bootstrap,
            {
                "protocol",
                "type",
                "credential",
                "installation_id",
                "resource_id",
                "environment_id",
                "adapter",
                "agent_id",
                "identity",
            },
        )
        identity = ResourceIdentity(
            bootstrap["installation_id"], bootstrap["resource_id"], bootstrap["agent_id"]
        )
        if (
            bootstrap["type"] != "bootstrap"
            or type(bootstrap["protocol"]) is not int
            or bootstrap["protocol"] != 1
            or any(bootstrap[key] != value for key, value in identity.fields().items())
            or type(bootstrap["credential"]) is not str
            or CREDENTIAL.fullmatch(bootstrap["credential"]) is None
        ):
            raise TransportError("executor_protocol_invalid")
        credential = bootstrap["credential"]
        journal = Journal(os.getcwd(), identity)
        write_frame(
            stream,
            {
                "protocol": 1,
                "type": "ready",
                "resource_id": identity.resource_id,
                "pid": os.getpid(),
                "capabilities": ["pure_text"],
            },
            deadline,
        )
        command = read_frame(stream, deadline)
        authenticate(command, credential)
        if command.get("type") == "inspect":
            exact(command, {"type", "credential", "envelope"})
            request = envelope(command["envelope"], identity)
            prior = journal.lookup(request)
            status, output = prior if prior is not None else ("missing", None)
            write_frame(stream, state(request, status, output, cached=True), deadline)
            return
        exact(command, {"type", "credential", "envelope", "arguments"})
        if command["type"] != "invoke":
            raise TransportError("executor_protocol_invalid")
        request = envelope(command["envelope"], identity)
        value = arguments(command["arguments"], request["input_hash"])
        prior = journal.lookup(request)
        if prior is not None:
            write_frame(stream, state(request, *prior, cached=True), deadline)
            return
        current_deadline(request)
        journal.intent(request)
        nonce = str(uuid4())
        prepared = {
            "protocol": 1,
            "type": "prepared",
            "action_id": request["action_id"],
            "request_id": request["request_id"],
            "request_hash": digest(request),
            "nonce": nonce,
        }
        write_frame(stream, prepared, deadline)
        permit = read_frame(stream, deadline)
        authenticate(permit, credential)
        exact(permit, {"protocol", "type", "credential", "request_hash", "nonce"})
        if (
            type(permit["protocol"]) is not int
            or permit["protocol"] != 1
            or permit["request_hash"] != digest(request)
            or permit["nonce"] != nonce
        ):
            raise TransportError("executor_permit_invalid")
        if permit["type"] == "stop":
            journal.stop(request)
            write_frame(stream, state(request, "stopped", cached=False), deadline)
            return
        if permit["type"] != "go":
            raise TransportError("executor_permit_invalid")
        current_deadline(request)
        journal.start(request)
        started = True
        observed = result(normalize(value))
        # Receipt é durável antes da resposta; perder IPC nunca autoriza repetir.
        journal.finish(request, observed)
        started = False
        write_frame(stream, state(request, "receipt", observed, cached=False), deadline)
    except Exception as error:
        if started and journal is not None:
            try:
                journal.unknown(request)
            except Exception:
                pass
        code = error.code if isinstance(error, TransportError) else "executor_internal_error"
        try:
            write_frame(stream, {"protocol": 1, "type": "error", "code": code}, deadline)
        except Exception:
            pass
    finally:
        if journal is not None:
            journal.close()


def main():
    # O descritor não é credencial. O socket já vem conectado do processo pai.
    if os.name != "posix" or len(sys.argv) != 2:
        raise SystemExit(2)
    try:
        descriptor = int(sys.argv[1])
        if descriptor < 3:
            raise ValueError()
        with socket.socket(fileno=descriptor) as stream:
            serve(stream)
    except ValueError, OSError:
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
