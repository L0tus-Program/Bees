"""CLI interna do launcher; setup e decisão humana ficam na interface web."""

import argparse
import sys
import time
from pathlib import Path

from bees_host.errors import HostError
from bees_host.runtime import Runtime
from bees_host.security import native_cipher
from bees_host.state import StateStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ponte de diagnóstico local Bees")
    parser.add_argument("--bootstrap-file", type=Path)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--new-pair", action="store_true")
    args = parser.parse_args(argv)
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
