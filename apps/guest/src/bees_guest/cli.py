"""CLI interna do futuro provisionador; sem instalação/elevação ou ações remotas."""

import argparse
import json
import sys
from pathlib import Path

from bees_guest.errors import GuestError
from bees_guest.journal import Journal
from bees_guest.runtime import GuestClient, connect
from bees_guest.security import load_identity, tls_context


def main(argv=None):
    parser = argparse.ArgumentParser(description="Diagnóstico TLS do guest Bees")
    parser.add_argument("--identity-dir", type=Path, required=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise GuestError("linux_required")
        identity = load_identity(args.identity_dir)
        context = tls_context(args.identity_dir)
        if args.check:
            print(json.dumps({"configured": True, "vm_ready": False}))
            return 0
        journal = Journal(args.identity_dir / "diagnostic.sqlite3", identity)
        try:
            with connect(identity, context) as stream:
                healthy = GuestClient(identity, journal).run(stream, once=args.once)
            print(json.dumps({"health_exchange": healthy, "vm_ready": False}))
            return 0 if healthy else 1
        finally:
            journal.close()
    except GuestError as error:
        print("Bees guest: " + error.code, file=sys.stderr)
        return 1
    except (OSError, ValueError):
        print("Bees guest: configuration_unavailable", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
