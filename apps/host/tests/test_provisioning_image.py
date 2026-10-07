"""Bytes pequenos descartáveis: os pins mudam somente na fixture, nunca no plano do usuário."""

import hashlib
import json
import os

import pytest
from bees_host.provisioning import image
from bees_host.provisioning.contracts import Plan, ProvisionError
from bees_host.provisioning.journal import create
from pydantic import ValidationError
from test_guest_bridge_tls import private_bridge_directory
from test_provisioning_runner import claim_fixture


@pytest.fixture
def template(monkeypatch):
    with private_bridge_directory() as base:
        entries = {}
        for name, prefix, data in (
            (image.ISO_NAME, "ISO", b"official-iso-fixture"),
            (image.PAYLOAD_NAME, "PAYLOAD", b"payload-fixture"),
            ("payload-inventory.json", "INVENTORY", b'{"fixture":true}'),
        ):
            digest = hashlib.sha256(data).hexdigest()
            monkeypatch.setattr(image, prefix + "_SHA256", digest)
            monkeypatch.setattr(image, prefix + "_SIZE", len(data))
            create(base / name, data)
            entries[name] = {"size": len(data), "sha256": digest}
        info = {
            "format": 1,
            "template_id": "linux-desktop-v1",
            "installation_mode": "human-confirmation",
            "base_iso": {"filename": image.ISO_NAME, **entries[image.ISO_NAME]},
            "source_sha256": {"scripts/packaging/guest-image.json": image.RECIPE_SHA256},
        }
        create(base / "kit-info.json", json.dumps(info).encode())
        create(base / "SHA256SUMS.json", json.dumps(entries).encode())
        yield base, info, entries


def test_template_verifies_all_bytes_without_writing(template):
    base, _, _ = template
    before = {path.name: path.read_bytes() for path in base.iterdir()}
    assert image.LocalTemplate(base).verify(claim_fixture().plan) == base / image.ISO_NAME
    assert {path.name: path.read_bytes() for path in base.iterdir()} == before


@pytest.mark.parametrize("name", [image.ISO_NAME, image.PAYLOAD_NAME, "payload-inventory.json"])
@pytest.mark.parametrize("failure", ["hash", "size", "missing"])
def test_changed_or_missing_bytes_fail_closed(template, name, failure):
    base, _, _ = template
    target = base / name
    if failure == "missing":
        target.unlink()
    else:
        original = target.read_bytes()
        target.write_bytes(b"x" * len(original) if failure == "hash" else original + b"x")
    with pytest.raises(ProvisionError, match="provision_(image_invalid|private_required)"):
        image.LocalTemplate(base).verify(claim_fixture().plan)


@pytest.mark.parametrize("failure", ["bool", "autoinstall", "recipe", "filename", "checksum"])
def test_untrusted_metadata_cannot_change_recipe_or_image(template, failure):
    base, info, entries = template
    if failure == "bool":
        info["format"] = True
    elif failure == "autoinstall":
        info["installation_mode"] = "autoinstall"
    elif failure == "recipe":
        info["source_sha256"]["scripts/packaging/guest-image.json"] = "0" * 64
    elif failure == "filename":
        info["base_iso"]["filename"] = "personal-disk.iso"
    else:
        entries["payload-inventory.json"]["sha256"] = "0" * 64
    (base / "kit-info.json").write_text(json.dumps(info))
    (base / "SHA256SUMS.json").write_text(json.dumps(entries))
    with pytest.raises(ProvisionError, match="provision_image_invalid"):
        image.LocalTemplate(base).verify(claim_fixture().plan)


def test_hardlinked_image_is_not_a_private_template(template):
    base, _, _ = template
    os.link(base / image.ISO_NAME, base / "personal-alias.iso")
    with pytest.raises(ProvisionError, match="provision_private_required"):
        image.LocalTemplate(base).verify(claim_fixture().plan)


@pytest.mark.parametrize(
    "field", ["storage_root", "shell", "vm_id", "generation", "provisioner_id"]
)
def test_plan_cannot_supply_paths_commands_or_dispatch_identity(field):
    value = claim_fixture().plan.model_dump(mode="json")
    value[field] = "private-untrusted-input"
    with pytest.raises(ValidationError) as failure:
        Plan.model_validate_json(json.dumps(value))
    assert "private-untrusted-input" not in str(failure.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [("boot", True), ("network", "nat"), ("cpu_count", 5), ("memory_bytes", 1), ("disk_bytes", 1)],
)
def test_plan_cannot_expand_hardware_or_boot(field, value):
    plan = claim_fixture().plan.model_dump(mode="json")
    plan[field] = value
    with pytest.raises(ValidationError):
        Plan.model_validate_json(json.dumps(plan))
