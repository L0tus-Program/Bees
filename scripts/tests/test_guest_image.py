"""Casos negativos do kit: autenticidade, arquivos hostis e preservação de estado."""

import hashlib
import importlib.util
import io
import json
import tarfile
import urllib.request
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "bees_guest_build", Path(__file__).parents[1] / "build-guest-image.py"
)
BUILD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILD)


def test_trust_anchor_and_pins():
    lock = BUILD.load_lock()
    assert len(BUILD.key_blob(lock)) > 1000
    assert lock["installation_mode"] == "human-confirmation"
    assert lock["iso"]["url"].startswith("https://cdimage.debian.org/")


def test_signature_requires_primary_signer_and_signed_digest():
    lock = BUILD.load_lock()
    manifest = f"{lock['iso']['sha256']}  {lock['iso']['filename']}\n"
    status = "[GNUPG:] VALIDSIG " + lock["signing_fingerprint"] + " 2026-10-05 0 0 4 0 1 8 00"
    BUILD.signed_manifest(manifest, status, lock)
    with pytest.raises(ValueError):
        BUILD.signed_manifest(manifest, status.replace(lock["signing_fingerprint"], "A" * 40), lock)
    with pytest.raises(ValueError):
        BUILD.signed_manifest(manifest.replace(lock["iso"]["sha256"], "0" * 64), status, lock)
    with pytest.raises(ValueError):
        BUILD.signed_manifest(manifest, status + "\n[GNUPG:] BADSIG untrusted", lock)
    with pytest.raises(ValueError):
        BUILD.signed_manifest(manifest + manifest, status, lock)


@pytest.mark.parametrize(
    "name", ["../outside", "/absolute", "a/../../b", "a\\b", "a/.env", "data/x"]
)
def test_private_and_traversal_names_rejected(name):
    with pytest.raises(ValueError):
        BUILD.public_name(name)


def test_output_cannot_escape_through_parent_segments():
    with pytest.raises(ValueError):
        BUILD.checked_output(BUILD.ROOT / "dist/../data")


def test_nonmatching_iso_never_accepted(tmp_path):
    source = tmp_path / "image.iso"
    source.write_bytes(b"incomplete")
    with pytest.raises(ValueError):
        BUILD.verify_iso(source, BUILD.load_lock())


def test_public_key_replacement_is_rejected(tmp_path, monkeypatch):
    key = tmp_path / "key.asc"
    key.write_bytes(BUILD.KEY.read_bytes() + b"changed")
    monkeypatch.setattr(BUILD, "KEY", key)
    with pytest.raises(ValueError):
        BUILD.key_blob(BUILD.load_lock())


class Response(io.BytesIO):
    status = 200
    headers = {}


class Opener:
    def open(self, request, timeout):
        return Response(b"download-test")


def test_download_preserves_existing_file_and_removes_own_partial(tmp_path, monkeypatch):
    monkeypatch.setattr(BUILD.urllib.request, "build_opener", lambda *args: Opener())
    target = tmp_path / "download"
    target.write_bytes(b"existing-user-file")
    with pytest.raises(FileExistsError):
        BUILD.download(BUILD.load_lock()["iso"]["url"], target, limit=100)
    assert target.read_bytes() == b"existing-user-file"
    target.unlink()
    with pytest.raises(ValueError):
        BUILD.download(BUILD.load_lock()["iso"]["url"], target, limit=4)
    assert not target.exists()


def test_download_never_contacts_alternative_url(tmp_path, monkeypatch):
    monkeypatch.setattr(BUILD.urllib.request, "build_opener", lambda *args: pytest.fail("network"))
    with pytest.raises(ValueError):
        BUILD.download("https://attacker.invalid/iso", tmp_path / "file", limit=1)


@pytest.mark.parametrize("change", ["http", "evil-host", "version", "query", "credentials", "port"])
def test_mirror_redirect_cannot_change_origin_contract(change):
    original = BUILD.load_lock()["iso"]["url"]
    target = original.replace("cdimage.debian.org", "saimei.ftp.acc.umu.se")
    handler = BUILD.DebianRedirect()
    request = urllib.request.Request(original)
    assert handler.redirect_request(request, None, 302, "", {}, target).full_url == target
    if change == "http":
        target = target.replace("https:", "http:")
    elif change == "evil-host":
        target = target.replace("saimei.ftp.acc.umu.se", "saimei.ftp.acc.umu.se.attacker.invalid")
    elif change == "version":
        target = target.replace("13.7.0", "13.8.0")
    elif change == "query":
        target += "?untrusted=yes"
    elif change == "credentials":
        target = target.replace("https://", "https://user:password@")
    else:
        target = target.replace("umu.se/", "umu.se:8080/")
    with pytest.raises(ValueError):
        handler.redirect_request(request, None, 302, "", {}, target)


def make_payload(
    tmp_path, *, extra=None, script=b"#!/bin/sh\nexit 0\n", mode=0o755, owner=0, file_mode=0o644
):
    files = {
        "install.sh": script,
        "packages.lock": b"pkg 1\n",
        "packages.json": b"{}",
        "SHA256SUMS": b"",
    }
    archive, inventory = tmp_path / "payload.tar.gz", tmp_path / "inventory.json"
    with tarfile.open(archive, "w:gz") as tar:
        for name, data in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = mode if name == "install.sh" else file_mode
            member.uid = member.gid = owner
            tar.addfile(member, io.BytesIO(data))
        if extra is not None:
            tar.addfile(extra, io.BytesIO(b"x") if extra.isfile() else None)
    inventory.write_text(
        json.dumps(
            {
                "format": 1,
                "template_id": "linux-desktop-v1",
                "files": {
                    name: {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                    for name, data in files.items()
                },
            }
        )
    )
    return archive, inventory


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "traversal", "duplicate", "writable-dir"]
)
def test_hostile_tar_entries_fail_without_extraction(tmp_path, kind):
    member = tarfile.TarInfo("extra")
    member.mode = 0o644
    if kind == "symlink":
        member.type, member.linkname = tarfile.SYMTYPE, "/etc/shadow"
    elif kind == "hardlink":
        member.type, member.linkname = tarfile.LNKTYPE, "install.sh"
    elif kind == "fifo":
        member.type = tarfile.FIFOTYPE
    elif kind == "traversal":
        member.name, member.size = "../outside", 1
    elif kind == "duplicate":
        member.name, member.size = "install.sh", 1
    else:
        member.type, member.mode = tarfile.DIRTYPE, 0o777
    archive, inventory = make_payload(tmp_path, extra=member)
    with pytest.raises(ValueError):
        BUILD.verify_payload(archive, inventory)
    assert not (tmp_path.parent / "outside").exists()


@pytest.mark.parametrize("script,mode", [(b"#!/bin/sh\r\n", 0o755), (b"#!/bin/sh\n", 0o4755)])
def test_invalid_installer_refused(tmp_path, script, mode):
    archive, inventory = make_payload(tmp_path, script=script, mode=mode)
    with pytest.raises(ValueError):
        BUILD.verify_payload(archive, inventory)


@pytest.mark.parametrize("owner,file_mode", [(10001, 0o644), (0, 0o664)])
def test_root_archive_requires_private_ownership_and_modes(tmp_path, owner, file_mode):
    archive, inventory = make_payload(tmp_path, owner=owner, file_mode=file_mode)
    with pytest.raises(ValueError):
        BUILD.verify_payload(archive, inventory)


@pytest.mark.parametrize("field", ["snapshot", "builder_image", "snapshot_inrelease_sha256"])
def test_updated_pin_requires_matching_recipe(field):
    lock = BUILD.load_lock()
    source = BUILD.ROOT / "runtime/linux-desktop"
    BUILD.validate_recipe(source, lock)
    if field == "snapshot":
        lock[field] = "20261006T000000Z"
    elif field == "builder_image":
        lock[field] = "debian:13.7-slim@sha256:" + "0" * 64
    else:
        lock[field]["trixie"] = "0" * 64
    with pytest.raises(ValueError):
        BUILD.validate_recipe(source, lock)


def test_existing_kit_not_overwritten(tmp_path, monkeypatch):
    output = tmp_path / "dist/guest-image"
    package = output / "linux-desktop-v1"
    package.mkdir(parents=True)
    state = package / "human-state.txt"
    state.write_bytes(b"preserve")
    monkeypatch.setattr(BUILD, "ROOT", tmp_path)
    monkeypatch.setattr(BUILD, "OUTPUT", output)
    monkeypatch.setattr(BUILD, "PACKAGE", package)
    with pytest.raises(ValueError):
        BUILD.build()
    assert state.read_bytes() == b"preserve"


def test_failed_build_cleans_only_own_stage(tmp_path, monkeypatch):
    output = tmp_path / "dist/guest-image"
    output.mkdir(parents=True)
    preserved = output / "keep.txt"
    preserved.write_text("keep")
    monkeypatch.setattr(BUILD, "ROOT", tmp_path)
    monkeypatch.setattr(BUILD, "OUTPUT", output)
    monkeypatch.setattr(BUILD, "PACKAGE", output / "linux-desktop-v1")
    monkeypatch.setattr(
        BUILD, "source_context", lambda stage: (_ for _ in ()).throw(ValueError("fail"))
    )
    with pytest.raises(ValueError):
        BUILD.build()
    assert list(output.iterdir()) == [preserved]


def test_concurrent_build_lock_preserved(tmp_path, monkeypatch):
    output = tmp_path / "dist/guest-image"
    output.mkdir(parents=True)
    lock = output / ".builder.lock"
    lock.write_text("another-build")
    monkeypatch.setattr(BUILD, "ROOT", tmp_path)
    monkeypatch.setattr(BUILD, "OUTPUT", output)
    monkeypatch.setattr(BUILD, "PACKAGE", output / "linux-desktop-v1")
    with pytest.raises(FileExistsError):
        BUILD.build()
    assert lock.read_text() == "another-build"
