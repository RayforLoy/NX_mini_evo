"""Compare real ARM flash/lens predicates under explicit synthetic input states."""
import json
from pathlib import Path

from nxmini.probe import ArmProbe, OBJECT, eeprom_catalog
from nxmini.util import write_json


def main():
    root = Path("work/nxmini-1.10-verified/decoded")
    if not root.exists():
        root = Path("work/nxmini-1.10/decoded")
    code = (root / "chunk-01.bin").read_bytes()
    catalog = eeprom_catalog((root / "chunk-04.bin").read_bytes())
    names = {row["id"]: row["name"] for row in catalog}
    rows = []
    for mode in (0x2002, 0x2003, 0x2004, 0x2005, 0x2006):
        for selector in (9, 10, 0x12, 0x18):
            result = ArmProbe(code, mode=mode).call(0x403eea24, OBJECT, selector)
            rows.append({"mode": hex(mode), "selector": hex(selector),
                         "setting_id": hex(result["result"]), "name": names.get(result["result"]),
                         "dependencies": result["dependencies"]})
    flash_rows = []
    for detached, lens_type, flag in ((False, 1, False), (True, 0, False), (True, 0, True), (False, 2, False)):
        for drive in (0, 3):
            settings = {0x161: 4, 0x159: drive}
            result = ArmProbe(code, mode=0x2006, detached=detached, lens_type=lens_type,
                              nolens_flag=flag, settings=settings).call(0x40347fe8, OBJECT)
            flash_rows.append({"mode": "0x2006", "detached": detached, "modelled_lens_type": lens_type,
                               "nolens_flag": flag, "modelled_settings": settings, **result})
    write_json(Path("research/generated/eeprom-catalog.json"), {
        "schema_version": 1, "section4_analysis_base": "0x40d45000",
        "mapping_hardware_verified": False, "entries": catalog})
    write_json(Path("research/generated/capture-probes.json"), {
        "schema_version": 1, "status": "bounded-arm-emulation-with-modelled-inputs",
        "hardware_verified": False, "unlisted_setting_values_modelled_as": 0,
        "selector_queries": rows, "flash_mode_queries": flash_rows})
    print(json.dumps({"eeprom_entries": len(catalog), "selector_queries": len(rows),
                      "flash_mode_queries": len(flash_rows), "hardware_verified": False}))


if __name__ == "__main__":
    main()
