"""Independently compare decoded regions of an existing experimental image."""
import json
from pathlib import Path

from nxmini import baseline, relc
from nxmini.firmware import Firmware
from nxmini.util import sha256, write_json


def main():
    manifest = json.loads(Path("experiments/non-xm-flash-qualification.json").read_text())
    original = Firmware.read("data/firmware/DATANXmini.bin")
    modified = Firmware.read("work/experiments/flash_qualification_DO_NOT_FLASH.bin")
    if sha256(original.data) != baseline.FIRMWARE_SHA256 or sha256(modified.data) != manifest["output_sha256"]:
        raise ValueError("Image hashes differ from the experiment manifest")
    op = manifest["operations"][0]
    sections = []
    for first, second in zip(original.sections, modified.sections):
        a = relc.decode(original.section_bytes(first))
        b = relc.decode(modified.section_bytes(second))
        expected = bytearray(a)
        if first.number == op["section"]:
            start = op["offset"]
            before, after = bytes.fromhex(op["before"]), bytes.fromhex(op["after"])
            assert a[start:start + len(before)] == before
            expected[start:start + len(after)] = after
        if b != expected:
            raise ValueError("Unexpected decoded change in section " + str(first.number))
        sections.append({"section": first.number, "decoded_bytes": len(b),
                         "original_sha256": sha256(a), "modified_sha256": sha256(b),
                         "matches_intended_edit": True})
    if any(original.resource_bytes(r.name) != modified.resource_bytes(r.name) for r in original.resources):
        raise ValueError("Experiment unexpectedly changed resources")
    writer = slice(0x400, original.header["code_offset"])
    assert original.data[writer] == modified.data[writer]
    report = modified.report()
    if not report["valid"]:
        raise ValueError("Experimental image has invalid known checksums")
    write_json(Path("research/generated/flash-qualification-experiment.json"), {
        "schema_version": 1, "status": "offline-instruction-experiment-verified",
        "input_sha256": sha256(original.data), "output_sha256": sha256(modified.data),
        "operation": op, "sections": sections,
        "all_163_resources_unchanged": True, "writer_unchanged": True,
        "checksums": report["checksums"], "footer_layout_preserved": report["footer_layout_preserved"],
        "hardware_verified": False, "flash_ready": False,
        "no_contact_short_capture_fixed": False, "smart_auto_fixed": False,
        "selfshot_flash_fixed": False,
    })
    print(json.dumps({"decoded_changes_match_manifest": True, "resources_unchanged": True,
                      "known_checksums_valid": True, "flash_ready": False}))


if __name__ == "__main__":
    main()
