"""Permite validar pacote novo com a .venv da instalação em desenvolvimento."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
