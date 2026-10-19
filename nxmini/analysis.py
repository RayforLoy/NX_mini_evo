"""Evidence extraction; file offsets are never presented as runtime addresses."""
from dataclasses import asdict
import re
import struct

from .firmware import Firmware
from . import relc
from .util import sha256, write_json

PATTERNS = {
    "manual_lens": r"nolens|NO_LENS|isLensDetached|isKMountAttached|MSG_FLASH_NX_MOUNT|lens.*(?:detect|mount|pin_check)",
    "selfshot": r"self.?shot|SELF-SHOT|Wink.Shot|FlashEnable|FlashEnable|flash.*(?:disable|enable)",
    "update": r"Checksum\(|updater_check|F/W File|firmware.*(?:update|version)",
    "rtos": r"REALOS|REAL OS|ITRON|PrCONNECT|PrConnect",
    "network": r"MobileLink|AutoShare|AutoBackup|autobackup|/mode/control|/smp_|socket|Dlna|DLNA|hostapd",
    "image_pipeline": r"IQ_CAP|Gamma|gamma|ColorMatrix|color.?matrix|LUT|PictureWizard|picture.?wizard",
    "lens_protocol": r"LENS_IF|LensIF|lens.*(?:cmd|baud|sio|uart)|CSIO|lens.*(?:iris|focus|ois|serial)",
}


def strings(data: bytes, minimum: int = 6):
    for match in re.finditer(rb"[\x20-\x7e]{%d,}" % minimum, data):
        yield {"offset": match.start(), "text": match.group().decode("ascii")}


def factory_catalog(xml: bytes) -> list[dict]:
    # The vendor file is not valid XML (e.g. <8step_drive>); preserve names using
    # its line-oriented format instead of silently dropping invalid elements.
    text = xml.decode("utf-8")
    result = []
    group, cmd = None, None
    for line in text.splitlines():
        parent = re.search(r'<([\w]+)\s+cmd_id="(0x[0-9a-fA-F]+)"', line)
        if parent:
            group, cmd = parent.group(1), int(parent.group(2), 16)
        leaf = re.search(r'<([\w]+)\s+index_id="(0x[0-9a-fA-F]+)">([^<]*)</', line)
        if leaf and group:
            result.append({
                "id": f"factory.{group}.{leaf.group(1)}", "group": group,
                "command_id": cmd, "index_id": int(leaf.group(2), 16),
                "tag": leaf.group(1), "label": leaf.group(3),
                "status": "static-command-table-only", "executable": False,
                "evidence": "SF_RESOURCE/PRD_CMD.XML", "camera_menu_implemented": False,
            })
    return result


def analyze(firmware: Firmware, output):
    output.mkdir(parents=True, exist_ok=True)
    report = firmware.report(deep=True)
    write_json(output / "firmware.json", report)
    catalog = factory_catalog(firmware.resource_bytes("PRD_CMD.XML"))
    write_json(output / "factory-catalog.json", {"entries": catalog,
               "complete_hidden_menu_inventory": False, "camera_menu_implemented": False})
    evidence = {name: [] for name in PATTERNS}
    import json
    with (output / "strings.jsonl").open("w", encoding="utf-8") as stream:
        for section in firmware.sections:
            data = relc.decode(firmware.section_bytes(section))
            for item in strings(data):
                row = {"section": section.number, "decoded_offset": item["offset"],
                       "text": item["text"], "kind": "static-string"}
                stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                for name, pattern in PATTERNS.items():
                    if re.search(pattern, item["text"], re.I):
                        evidence[name].append(row)
    write_json(output / "evidence.json", {
        "firmware_sha256": sha256(firmware.data), "address_space": "decoded-section-offsets",
        "runtime_address_verified": False, "categories": evidence,
    })
    return {"firmware_valid": report["valid"], "factory_commands": len(catalog),
            "evidence_counts": {name: len(rows) for name, rows in evidence.items()}}


def disassemble(data: bytes, offset: int, size: int, base: int, mode: str) -> str:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM, CS_MODE_THUMB
    if offset < 0 or size <= 0 or offset + size > len(data):
        raise ValueError("Disassembly range is outside input")
    decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB if mode == "thumb" else CS_MODE_ARM)
    decoder.skipdata = True
    return "\n".join(f"{i.address:08x}  {i.bytes.hex():12} {i.mnemonic:9} {i.op_str}"
                     for i in decoder.disasm(data[offset:offset + size], base + offset)) + "\n"
