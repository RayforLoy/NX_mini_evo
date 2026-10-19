import json
from pathlib import Path
import struct

import pytest

from nxmini import baseline, checksums, relc
from nxmini.errors import FirmwareError
from nxmini.firmware import Firmware, rebuild
from nxmini.patching import apply
from nxmini.util import resource_filename, sha256, safe_child


def fixture():
    data = bytearray(0x1100 + 20)
    struct.pack_into("<5I", data, 0, 0x400, 0x400, 0x40000400, 0x90, 0xe1)
    section = relc.encode(b"abcdefgh" * 128)
    struct.pack_into("<II5s12s11s9s", data, 0x185, 0x202, 0x800,
                     b"01.10", b"201501162119", b"GLUAOA2", b"NXMINI")
    struct.pack_into("<III", data, 0x1b2, 1, 1, len(section))
    struct.pack_into("<I", data, 0x256, len(data) - 20)
    data[0x800:0x800 + len(section)] = section
    data[0x1000:0x1010] = b"SF_RESOURCE\0\0\0\0\0"
    struct.pack_into("<I", data, 0x1014, 1)
    struct.pack_into("<56sII", data, 0x1020, b"test.bin", 0, 4)
    data[0x1060:0x1064] = b"test"
    struct.pack_into("<I", data, len(data) - 16, len(data) - 20)
    data[-12] = 0xfc
    return checksums.repair(bytes(data), 0x800, 0x202, len(data) - 20)


def test_fixture_checksums_and_corruption():
    data = fixture()
    assert Firmware(data).report(deep=True)["valid"]
    changed = bytearray(data)
    changed[0x1060] ^= 1
    assert not Firmware(bytes(changed)).report()["valid"]


@pytest.mark.parametrize("edit", ["truncate", "model", "section", "resource", "filename", "count", "size"])
def test_malformed_container(edit):
    data = bytearray(fixture())
    if edit == "truncate":
        data = data[:100]
    elif edit == "model":
        data[0x1a9:0x1b2] = b"OTHER\0\0\0\0"
    elif edit == "section":
        struct.pack_into("<I", data, 0x1ba, 0xfffffff)
    elif edit == "resource":
        struct.pack_into("<I", data, 0x105c, 0xfffffff)
    elif edit == "filename":
        data[0x1020:0x1028] = b"../x.bin"
    elif edit == "count":
        struct.pack_into("<I", data, 0x1b2, 13)
    elif edit == "size":
        struct.pack_into("<I", data, 0x256, 1)
    with pytest.raises(FirmwareError):
        Firmware(bytes(data))


def test_extract_rebuild_and_resource_edit(tmp_path):
    data = fixture()
    folder = tmp_path / "extraction"
    Firmware(data).extract(folder)
    assert rebuild(folder) == data
    (folder / "resources" / "test.bin").write_bytes(b"TEST")
    rebuilt = rebuild(folder)
    assert Firmware(rebuilt).resource_bytes("test.bin") == b"TEST"
    assert Firmware(rebuilt).report()["valid"]
    (folder / "resources" / "test.bin").write_bytes(b"longer")
    with pytest.raises(FirmwareError):
        rebuild(folder)


def test_decoded_rebuild(tmp_path):
    folder = tmp_path / "extraction"
    Firmware(fixture()).extract(folder)
    decoded = folder / "decoded" / "chunk-01.bin"
    data = decoded.read_bytes()
    decoded.write_bytes(b"A" + data[1:])
    changed = Firmware(rebuild(folder))
    assert relc.decode(changed.section_bytes(changed.sections[0])) == b"A" + data[1:]


def test_patch_guards_and_resource_reversal():
    data = fixture()
    manifest = {"schema_version": 1, "model": "NXMINI", "version": "01.10",
                "input_sha256": sha256(data), "operations": [
                    {"space": "resource", "resource": "test.bin", "offset": 0,
                     "before": b"test".hex(), "after": b"TEST".hex()}]}
    changed, receipt = apply(data, manifest)
    assert not receipt["flash_ready"]
    manifest["output_sha256"] = sha256(changed)
    assert apply(changed, manifest, reverse=True)[0] == data
    with pytest.raises(FirmwareError, match="SHA-256"):
        apply(changed, manifest)
    manifest["operations"][0]["before"] = b"nope".hex()
    with pytest.raises(FirmwareError, match="preimage"):
        apply(data, manifest)


def test_path_traversal(tmp_path):
    for name in ["../outside", "C:/outside", "a\\b", "/outside", "CON", "aux.jpg", "file."]:
        with pytest.raises(FirmwareError):
            safe_child(tmp_path, name)


def test_unknown_footer_mutation_is_not_valid():
    data = bytearray(fixture())
    data[-1] = 1
    report = Firmware(bytes(data)).report()
    assert report["checksums"]["file"]["valid"]
    assert not report["valid"]


def test_rebuild_rejects_invalid_original(tmp_path):
    data = bytearray(fixture())
    data[0x1060] ^= 1
    folder = tmp_path / "corrupt"
    Firmware(bytes(data)).extract(folder)
    with pytest.raises(FirmwareError, match="invalid original"):
        rebuild(folder)


def test_vendor_resource_name_mapping(tmp_path):
    data = bytearray(fixture())
    struct.pack_into("<56s", data, 0x1020, b"test.bin ")
    original = checksums.repair(bytes(data), 0x800, 0x202, len(data) - 20)
    folder = tmp_path / "extracted"
    firmware = Firmware(original)
    firmware.extract(folder)
    assert firmware.resources[0].name == "test.bin "
    assert (folder / "resources/test.bin%20").read_bytes() == b"test"
    assert rebuild(folder) == original
    assert resource_filename("CON") == "%43ON"
    assert resource_filename("file.") == "file%2E"
    assert resource_filename("file%20") == "file%2520"


@pytest.mark.firmware
def test_official_full_decode():
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Run nxmini fetch to supply the official firmware")
    report = Firmware.read(path).report(deep=True)
    assert report["sha256"] == baseline.FIRMWARE_SHA256
    assert report["valid"] and report["official_baseline"]
    assert len(report["resources"]) == 163
    assert sum(s["decoded_bytes"] for s in report["sections"]) == 21435384


@pytest.mark.firmware
def test_official_extract_and_roundtrip(tmp_path):
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    original = path.read_bytes()
    folder = tmp_path / "official"
    firmware = Firmware(original)
    firmware.extract(folder)
    assert len(list((folder / "resources").iterdir())) == 163
    for resource in firmware.resources:
        assert (folder / "resources" / resource_filename(resource.name)).read_bytes() == firmware.resource_bytes(resource.name)
    assert rebuild(folder) == original


@pytest.mark.firmware
def test_official_offline_section_patch():
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    original = path.read_bytes()
    manifest = {"schema_version": 1, "model": "NXMINI", "version": "01.10",
                "input_sha256": baseline.FIRMWARE_SHA256,
                "operations": [{"space": "section", "section": 3,
                                "offset": 11, "before": "00", "after": "01"}]}
    # Synthetic byte change validates the pipeline only. Never written to a camera.
    patched, receipt = apply(original, manifest)
    firmware = Firmware(patched)
    assert firmware.report()["valid"]
    section = next(s for s in firmware.sections if s.number == 3)
    assert relc.decode(firmware.section_bytes(section))[-1] == 1
    assert not receipt["flash_ready"]
    assert path.read_bytes() == original


@pytest.mark.firmware
def test_official_manual_lens_c01_reproduces_pinned_image_hash():
    import json
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    manifest = json.loads(Path("patches/manual-lens-c01.json").read_text())
    result, receipt = apply(path.read_bytes(), manifest)
    assert receipt["output_sha256"] == manifest["output_sha256"]
    assert receipt["recompression"] == "preserve-tokens-v1"
    assert not receipt["flash_ready"] and not receipt["hardware_verified"]


@pytest.mark.firmware
def test_official_manual_lens_c02_reproduces_pinned_image_hash():
    import json
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    manifest = json.loads(Path("patches/manual-lens-c02.json").read_text())
    result, receipt = apply(path.read_bytes(), manifest)
    assert receipt["output_sha256"] == manifest["output_sha256"]
    assert len(receipt["operations"]) == 4
    assert not receipt["flash_ready"] and not receipt["hardware_verified"]


@pytest.mark.firmware
def test_official_manual_lens_c03_reproduces_pinned_image_hash():
    import json
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    manifest = json.loads(Path("patches/manual-lens-c03.json").read_text())
    result, receipt = apply(path.read_bytes(), manifest)
    assert receipt["output_sha256"] == manifest["output_sha256"]
    assert len(receipt["operations"]) == 5
    assert not receipt["flash_ready"] and not receipt["hardware_verified"]


@pytest.mark.firmware
def test_official_manual_lens_c04_reproduces_pinned_image_hash():
    import json
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    manifest = json.loads(Path("patches/manual-lens-c04.json").read_text(encoding="utf-8"))
    result, receipt = apply(path.read_bytes(), manifest)
    assert receipt["output_sha256"] == "a8e71a56bc55bfa9706a977457429945ae9125e05422770caeb75cb8fa957fa8"
    assert len(receipt["operations"]) == 6
    assert not receipt["flash_ready"] and not receipt["hardware_verified"]


@pytest.mark.firmware
def test_official_manual_lens_c05_reproduces_pinned_image_hash():
    import json
    path = Path("data/firmware/DATANXmini.bin")
    if not path.exists():
        pytest.skip("Official firmware is not present")
    manifest = json.loads(Path("patches/manual-lens-c05.json").read_text(encoding="utf-8"))
    result, receipt = apply(path.read_bytes(), manifest)
    assert receipt["output_sha256"] == "bf49164f24781ff61392e2d9afdd01a236554773f4c681de884f56272094f60e"
    assert len(receipt["operations"]) == 7
    assert len(bytes.fromhex(receipt["operations"][-1]["after"])) == 28
    assert not receipt["flash_ready"] and not receipt["hardware_verified"]
