"""O serviço dos computadores declara tetos de CPU, memória e PIDs configuráveis."""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[2] / "compose.yaml"


def _service(name: str) -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(rf"^  {name}:\n(.*?)(?=^  [a-z-]+:\n|^volumes:)", text, re.M | re.S)
    assert match, name
    return match.group(1)


def test_computer_service_has_resource_ceilings_with_overrides():
    computer = _service("computer")
    for line in (
        "cpus: ${BEES_COMPUTER_CPUS:-2}",
        "mem_limit: ${BEES_COMPUTER_MEMORY:-4g}",
        "pids_limit: ${BEES_COMPUTER_PIDS:-16384}",
        "BEES_COMPUTER_MAX_PROCESSES: ${BEES_COMPUTER_MAX_PROCESSES:-8192}",
        "BEES_COMPUTER_MAX_FILE_MB: ${BEES_COMPUTER_MAX_FILE_MB:-4096}",
    ):
        assert line in computer
    # Sem portas publicadas nem pastas do host no serviço dos computadores.
    assert "ports:" not in computer
    assert not re.search(r"^\s+- [./~]", computer, re.M)
