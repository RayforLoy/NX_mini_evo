"""Hash-bound, reversible offline patches. Output is always a research artifact."""
import struct

from . import baseline, checksums, relc
from .errors import FirmwareError
from .firmware import Firmware
from .util import sha256


def apply(data: bytes, manifest: dict, *, reverse: bool = False) -> tuple[bytes, dict]:
    if manifest.get("schema_version") != 1:
        raise FirmwareError("Unsupported patch schema")
    expected_hash = manifest.get("output_sha256" if reverse else "input_sha256")
    if not expected_hash or sha256(data) != expected_hash:
        raise FirmwareError("Patch input SHA-256 does not match")
    if manifest.get("model") != baseline.MODEL or manifest.get("version") != baseline.VERSION:
        raise FirmwareError("Patch model/version does not match supported baseline")
    firmware = Firmware(data)
    if firmware.header["version"] != baseline.VERSION:
        raise FirmwareError("Firmware metadata version does not match patch")
    if not firmware.report()["valid"]:
        raise FirmwareError("Patch input has invalid checksums")
    operations = manifest.get("operations", [])
    if not operations:
        raise FirmwareError("Patch has no validated operations")
    recompression = manifest.get("recompression", "legacy-greedy-v1")
    if recompression not in ("legacy-greedy-v1", "preserve-tokens-v1"):
        raise FirmwareError("Unsupported patch recompression strategy")
    output = bytearray(data)
    sections = {}
    modified_regions = []
    for op in operations:
        kind = op.get("space")
        try:
            before = bytes.fromhex(op["after" if reverse else "before"])
            after = bytes.fromhex(op["before" if reverse else "after"])
            offset = int(op["offset"])
        except (KeyError, ValueError, TypeError) as error:
            raise FirmwareError("Invalid patch operation") from error
        if not before or len(before) != len(after) or offset < 0:
            raise FirmwareError("Patches must replace equal, nonzero byte lengths")
        if kind == "resource":
            resource = next((r for r in firmware.resources if r.name == op.get("resource")), None)
            if resource is None or offset + len(before) > resource.size:
                raise FirmwareError("Patch exceeds resource bounds")
            absolute = resource.offset + offset
            identity = ("file", absolute, absolute + len(before))
            target = output
            position = absolute
        elif kind == "section":
            number = op.get("section")
            section = next((s for s in firmware.sections if s.number == number), None)
            if section is None:
                raise FirmwareError("Patch section does not exist")
            if number not in sections:
                sections[number] = bytearray(relc.decode(firmware.section_bytes(section)))
            target = sections[number]
            position = offset
            identity = (f"section-{number}", offset, offset + len(before))
            if position + len(before) > len(target):
                raise FirmwareError("Patch exceeds decoded section bounds")
        else:
            raise FirmwareError("Only decoded-section and resource patches are supported")
        if any(space == identity[0] and start < identity[2] and identity[1] < end
               for space, start, end in modified_regions):
            raise FirmwareError("Patch operations overlap")
        modified_regions.append(identity)
        if bytes(target[position:position + len(before)]) != before:
            raise FirmwareError("Patch preimage bytes do not match")
        target[position:position + len(after)] = after
    for i, section in enumerate(firmware.sections):
        if section.number not in sections:
            continue
        if reverse:
            # Reproducing the exact old compressed stream cannot be guaranteed by a
            # different encoder. Reversal of code patches uses the saved original.
            raise FirmwareError("Reverse section patches by restoring the SHA-verified original file")
        encoded = relc.rewrite(firmware.section_bytes(section), bytes(sections[section.number]),
                               preserve_tokens=recompression == "preserve-tokens-v1")
        capacity = ((section.offset + section.size + 511) // 512 * 512) - section.offset
        if i == len(firmware.sections) - 1:
            capacity = firmware.header["code_offset"] + firmware.header["code_size"] - 2 - section.offset
        if len(encoded) > capacity:
            raise FirmwareError("Recompressed patch does not fit original section allocation")
        extent = max(section.size, len(encoded))
        if any(data[section.offset + section.size:section.offset + extent]):
            raise FirmwareError("Patch would overwrite nonzero section padding")
        output[section.offset:section.offset + extent] = encoded.ljust(extent, b"\0")
        struct.pack_into("<I", output, 0x1ba + i * 8, len(encoded))
    result = checksums.repair(bytes(output), firmware.header["code_offset"],
                              firmware.header["code_size"], firmware.footer_offset)
    verified = Firmware(result)
    if [s.offset for s in verified.sections] != [s.offset for s in firmware.sections]:
        raise FirmwareError("Patched section offsets changed")
    for s in verified.sections:
        decoded = relc.decode(verified.section_bytes(s))
        if s.number in sections and decoded != sections[s.number]:
            raise FirmwareError("Patched section failed decode verification")
    target_hash = manifest.get("input_sha256" if reverse else "output_sha256")
    if target_hash and sha256(result) != target_hash:
        raise FirmwareError("Patch output SHA-256 does not match manifest")
    receipt = {
        "schema_version": 1, "model": baseline.MODEL, "version": baseline.VERSION,
        "input_sha256": sha256(data), "output_sha256": sha256(result),
        "flash_ready": False, "status": "offline-research-only",
        "operations": operations, "recovery_verified": False,
        "recompression": recompression,
        "hardware_verified": False, "checksums": verified.report()["checksums"],
    }
    return result, receipt
