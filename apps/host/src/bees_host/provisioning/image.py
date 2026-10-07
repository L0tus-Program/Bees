"""Template privado pré-copiado pelo operador. Não baixa nem importa receitas externas."""

import hashlib
import json
from pathlib import Path

from bees_host.provisioning.contracts import (
    INVENTORY_SHA256,
    INVENTORY_SIZE,
    ISO_SHA256,
    ISO_SIZE,
    PAYLOAD_SHA256,
    PAYLOAD_SIZE,
    RECIPE_SHA256,
    Plan,
    ProvisionError,
)
from bees_host.provisioning.journal import private

ISO_NAME = "debian-13.7.0-amd64-netinst.iso"
PAYLOAD_NAME = "bees-linux-desktop-v1-payload.tar.gz"


def _hash(path: Path, size: int) -> str:
    private(path)
    if path.stat().st_size != size:
        raise ProvisionError("provision_image_invalid")
    result = hashlib.sha256()
    observed = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            observed += len(chunk)
            if observed > size:
                raise ProvisionError("provision_image_invalid")
            result.update(chunk)
    if observed != size:
        raise ProvisionError("provision_image_invalid")
    return result.hexdigest()


class LocalTemplate:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def verify(self, plan: Plan) -> Path:
        """Só leitura: hash fixado autentica bytes, não concede autorização Windows."""
        try:
            private(self.directory, directory=True)
            path = self.directory / "kit-info.json"
            private(path)
            if path.stat().st_size > 65536:
                raise ProvisionError("provision_image_invalid")
            info = json.loads(path.read_bytes())
            if (
                type(info["format"]) is not int
                or info["format"] != 1
                or info["template_id"] != plan.template_id
                or info["installation_mode"] != "human-confirmation"
                or info["base_iso"]["filename"] != ISO_NAME
                or info["base_iso"]["sha256"] != ISO_SHA256
                or info["base_iso"]["size"] != ISO_SIZE
                or info["source_sha256"]["scripts/packaging/guest-image.json"] != RECIPE_SHA256
            ):
                raise ProvisionError("provision_image_invalid")
            path = self.directory / "SHA256SUMS.json"
            private(path)
            if path.stat().st_size > 65536:
                raise ProvisionError("provision_image_invalid")
            checksums = json.loads(path.read_bytes())
            for name, size, digest in (
                (ISO_NAME, ISO_SIZE, ISO_SHA256),
                (PAYLOAD_NAME, PAYLOAD_SIZE, PAYLOAD_SHA256),
                ("payload-inventory.json", INVENTORY_SIZE, INVENTORY_SHA256),
            ):
                if checksums[name] != {"size": size, "sha256": digest}:
                    raise ProvisionError("provision_image_invalid")
                if _hash(self.directory / name, size) != digest:
                    raise ProvisionError("provision_image_invalid")
            return self.directory / ISO_NAME
        except OSError, ValueError, KeyError, TypeError:
            raise ProvisionError("provision_image_invalid") from None
