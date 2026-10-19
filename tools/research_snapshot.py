"""Regenerate small research inventories without redistributing firmware bytes."""
import argparse
from pathlib import Path

from nxmini import baseline
from nxmini.analysis import factory_catalog
from nxmini.firmware import Firmware
from nxmini.util import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("firmware", type=Path)
    parser.add_argument("--output", type=Path, default=Path("research/generated"))
    args = parser.parse_args()
    firmware = Firmware.read(args.firmware)
    report = firmware.report(deep=True)
    if not report["valid"] or report["sha256"] != baseline.FIRMWARE_SHA256:
        parser.error("Snapshot requires the hash-pinned official 1.10 firmware")
    write_json(args.output / "firmware.json", report)
    entries = factory_catalog(firmware.resource_bytes("PRD_CMD.XML"))
    write_json(args.output / "factory-catalog.json", {
        "schema_version": 1, "firmware_sha256": report["sha256"], "entries": entries,
        "entries_count": len(entries), "complete_hidden_menu_inventory": False,
        "camera_menu_implemented": False, "all_commands_executable": False,
    })
    print(f"Snapshot: {len(report['sections'])} sections, {len(report['resources'])} resources, {len(entries)} factory entries")


if __name__ == "__main__":
    main()
