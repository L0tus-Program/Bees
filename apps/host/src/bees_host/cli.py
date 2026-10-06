"""CLI interna do launcher; setup e decisão humana ficam na interface web."""

import argparse
import sys
import time
from pathlib import Path

from bees_host.errors import HostError
from bees_host.runtime import Runtime
from bees_host.security import check_private, native_cipher
from bees_host.state import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ponte de diagnóstico local Bees")
    parser.add_argument("--bootstrap-file", type=Path)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--check-private", type=Path)
    parser.add_argument("--directory", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--new-pair", action="store_true")
    args = parser.parse_args(argv)
    if args.check_private is not None:
        if (
            args.state_dir is not None
            or args.bootstrap_file is not None
            or args.once
            or args.new_pair
        ):
            parser.error("Não combine verificação privada com execução do runtime.")
        try:
            check_private(args.check_private.absolute(), directory=args.directory)
            return 0
        except HostError, OSError, ValueError:
            print("Bees host: private_path_required", file=sys.stderr)
            return 1
    if args.directory:
        parser.error("--directory exige --check-private.")
    if args.state_dir is None:
        parser.error("Informe --state-dir para executar o runtime.")
    try:
        store = StateStore(args.state_dir, native_cipher())
        with store.lock():
            store.initialize(args.bootstrap_file, new_pair=args.new_pair)
            runtime = Runtime(store)
            try:
                failures = 0
                while True:
                    try:
                        session = runtime.step()
                        failures = 0
                        if runtime.state.revoked or runtime.state.expired or args.once:
                            return 0
                    except HostError as error:
                        if error.code != "connection_unavailable" or args.once:
                            raise
                        # Somente handshake/report idempotentes; nunca cria novo pareamento.
                        failures += 1
                    time.sleep(min(15 * 2 ** min(failures, 3), 60))
            finally:
                runtime.close()
    except KeyboardInterrupt:
        return 0
    except HostError as error:
        if (
            error.code == "host_unauthorized"
            and "runtime" in locals()
            and (runtime.state.revoked or runtime.state.expired)
        ):
            return 0
        print("Bees host: " + error.code, file=sys.stderr)
        return 1
    except OSError, ValueError:
        print("Bees host: local_state_unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
