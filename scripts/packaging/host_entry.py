"""Entrada fechada do helper empacotado; não aceita código Python arbitrário."""

from bees_host.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
