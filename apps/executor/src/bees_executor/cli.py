"""CLI do executor: runtime de computadores (container) ou conector pessoal."""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from bees_executor.client import CredentialRejected, ExecutorClient, exchange, load_credential
from bees_executor.journal import Journal
from bees_executor.tools import ToolHost

log = logging.getLogger("bees_executor")


class ComputerRuntime:
    """Provisiona/remove computadores pedidos pela API e encaminha comandos."""

    capabilities = ["files", "shell", "browser", "office", "desktop"]

    def __init__(self, computers):
        self.computers = computers
        self.views = {}

    async def execute(self, message: dict) -> dict:
        computer_id = message.get("computer_id")
        if not isinstance(computer_id, str):
            return {"status": "failed", "code": "executor_protocol_invalid"}
        return await self.computers.execute(
            computer_id,
            {"id": message["id"], "tool": message["tool"], "arguments": message.get("arguments")},
        )

    async def control(self, client: ExecutorClient, message: dict) -> None:
        kind = message.get("type")
        if kind in ("welcome", "computers"):
            for computer in message.get("computers", []):
                await self._sync(client, computer)
        elif kind == "view_open":
            from bees_executor.view import open_view

            await open_view(client, self.computers, message)

    async def _sync(self, client, computer: dict) -> None:
        computer_id, status = computer.get("id"), computer.get("status")
        try:
            if status in ("provisioning", "ready"):
                await self.computers.provision(computer_id)
                if status == "provisioning":
                    await client.send(
                        {"type": "computer_status", "id": computer_id, "status": "ready"}
                    )
            elif status == "removing":
                await self.computers.remove(computer_id)
                await client.send(
                    {"type": "computer_status", "id": computer_id, "status": "removed"}
                )
        except Exception as error:
            log.warning("Falha ao preparar computador: %s", type(error).__name__)
            await client.send(
                {
                    "type": "computer_status",
                    "id": computer_id,
                    "status": "failed",
                    "reason": "provisioning_failed",
                }
            )


class PersonalConnector:
    capabilities = ["files", "shell", "browser", "office"]

    def __init__(self, host: ToolHost):
        self.host = host

    async def execute(self, message: dict) -> dict:
        return await asyncio.to_thread(
            self.host.execute,
            {
                "id": message["id"],
                "tool": message["tool"],
                "arguments": message.get("arguments"),
                "scope": message.get("scope") or {},
            },
        )

    async def control(self, client, message):
        return None


def _invite(args) -> str | None:
    if args.invite:
        return args.invite
    if args.invite_file and Path(args.invite_file).exists():
        try:
            return json.loads(Path(args.invite_file).read_text())["token"]
        except (ValueError, KeyError, OSError):
            return None
    return None


async def _run(args, handler, kind, roots=()):
    state = Path(args.state)
    used = None
    while True:
        credential = load_credential(state)
        if credential is None:
            token = _invite(args)
            if token is None or token == used:
                log.info("Aguardando convite do Bees…")
                await asyncio.sleep(3)
                continue
            try:
                credential = exchange(args.api, token, state)
                log.info("Executor pareado.")
            except CredentialRejected as error:
                # Recusado pela API: não tenta de novo o mesmo convite.
                used = token
                log.warning("Convite recusado: %s", error)
                await asyncio.sleep(3)
                continue
            except OSError as error:
                # API ainda subindo ou rede instável: o convite continua válido; tenta de novo.
                log.warning("API indisponível ao parear: %s", type(error).__name__)
                await asyncio.sleep(3)
                continue
        client = ExecutorClient(
            args.api, state, kind, handler, capabilities=handler.capabilities, roots=list(roots)
        )
        try:
            await client.run_forever(credential)
            return
        except CredentialRejected:
            log.warning("Credencial revogada; aguardando novo convite.")
            (state / "credential.json").unlink(missing_ok=True)
            await asyncio.sleep(3)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="bees-executor")
    sub = parser.add_subparsers(dest="kind", required=True)
    for name in ("computer", "personal"):
        command = sub.add_parser(name)
        command.add_argument(
            "--api", default=os.environ.get("BEES_API_URL", "http://127.0.0.1:8080")
        )
        command.add_argument(
            "--state", default=os.environ.get("BEES_EXECUTOR_STATE", "./executor-state")
        )
        command.add_argument("--invite", default=os.environ.get("BEES_EXECUTOR_INVITE"))
        command.add_argument("--invite-file", default=os.environ.get("BEES_EXECUTOR_INVITE_FILE"))
    computer = sub.choices["computer"]
    computer.add_argument("--computers", default=os.environ.get("BEES_COMPUTERS_DIR", "/computers"))
    computer.add_argument(
        "--mode",
        choices=["multiuser", "single"],
        default=os.environ.get("BEES_COMPUTER_MODE", "single"),
    )
    personal = sub.choices["personal"]
    personal.add_argument(
        "--root",
        action="append",
        default=[],
        help="Pasta compartilhável no formato Alias=Caminho (pode repetir).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.kind == "computer":
        from bees_executor.computers import MultiUserComputers, SingleComputers

        base = Path(args.computers)
        computers = (
            MultiUserComputers(base, Path(args.state))
            if args.mode == "multiuser"
            else SingleComputers(base)
        )
        handler = ComputerRuntime(computers)
        roots = ()
    else:
        configured = {}
        for item in args.root:
            alias, _, path = item.partition("=")
            if not alias or not path or "/" in alias:
                parser.error("Use --root Alias=Caminho")
            configured[alias] = Path(path).expanduser()
        state = Path(args.state)
        (state / "files").mkdir(parents=True, exist_ok=True)
        home = Path.home()
        host = ToolHost(
            configured or {"Bees": state / "files"},
            home,
            Journal(state / "journal.sqlite3"),
            named=True,
            env=ToolHost.default_env(home),
            browser_profile=state / "browser-profile",
            screenshots=state / "screenshots",
        )
        handler = PersonalConnector(host)
        roots = list((configured or {"Bees": None}).keys())
    try:
        asyncio.run(
            _run(
                args, handler, "computer_runtime" if args.kind == "computer" else "personal", roots
            )
        )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
