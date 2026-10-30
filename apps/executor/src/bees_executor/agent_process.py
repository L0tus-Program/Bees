"""Processo agente de um computador: roda com o UID da abelha e executa ferramentas.

Escuta somente um socket Unix dentro da home privada (0700). Cada linha JSON é
um comando; a resposta é o recibo correspondente.
"""

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from bees_executor.journal import Journal
from bees_executor.tools import ToolHost


async def serve(socket_path: Path, home: Path) -> None:
    host = ToolHost(
        {"workspace": home / "workspace"},
        home,
        Journal(home / ".bees" / "journal.sqlite3"),
        named=False,
        screenshots=home / "workspace" / "screenshots",
    )
    lock = asyncio.Lock()

    async def handle(reader, writer):
        try:
            line = await reader.readline()
            if not line:
                return
            command = json.loads(line)
            async with lock:
                receipt = await asyncio.to_thread(host.execute, command)
            writer.write(json.dumps(receipt, ensure_ascii=False).encode() + b"\n")
            await writer.drain()
        except (ValueError, KeyError):
            writer.write(b'{"status":"failed","code":"executor_protocol_invalid"}\n')
        except Exception:
            logging.getLogger("bees_executor").exception("Comando sem recibo")
            raise
        finally:
            writer.close()

    os.umask(0o077)
    server = await asyncio.start_unix_server(handle, path=str(socket_path))
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    parser.add_argument("--home", required=True)
    args = parser.parse_args()
    # stderr vai para ~/.bees/agent.log (aberto pelo supervisor): falhas ficam rastreáveis.
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    asyncio.run(serve(Path(args.socket), Path(args.home)))


if __name__ == "__main__":
    main()
