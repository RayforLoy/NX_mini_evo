"""Reproduce bounded original flash parameter writes; never operate a camera."""
import argparse
import json
from pathlib import Path
import struct

from nxmini import baseline
from nxmini.errors import FirmwareError
from nxmini.probe import BASE, FlashProbe, AeModeProbe
from nxmini.util import sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decoded", type=Path,
                        default=Path("work/nxmini-1.10-verified/decoded/chunk-01.bin"))
    parser.add_argument("--output", type=Path,
                        default=Path("research/generated/flash-parameter-probes.json"))
    args = parser.parse_args()
    code = args.decoded.read_bytes()
    if sha256(code) != baseline.DECODED_SHA256[1]:
        raise FirmwareError("Flash probe requires the original official 1.10 section 1")

    def word(address):
        return struct.unpack_from("<I", code, address - BASE)[0]

    # Startup constructs CCamSystemImpl, which replaces CCamSystem's abstract
    # main table and IQ subobject table. Using the base tables misses overrides.
    bindings = {
        "startup_global_literal_0x40248c78": (word(0x40248c78), 0x4098f1cc),
        "impl_table_literal_0x40267a1c": (word(0x40267a1c), FlashProbe.CAMERA_VTABLE),
        "derived_iq_slot_0xc8": (word(FlashProbe.IQ_VTABLE + 0xc8), 0x40695360),
        "derived_main_slot_0xf8": (word(FlashProbe.CAMERA_VTABLE + 0xf8), 0x406c1c94),
        "base_iq_state_literal_0x4040b41c": (word(0x4040b41c), FlashProbe.IQ_STATE),
        "app_mode_setter_slot_0x94": (word(0x40773cdc + 0x94), 0x404410e4),
        "derived_iq_scene_slot_0x98": (word(FlashProbe.IQ_VTABLE + 0x98), 0x40695040),
    }
    if any(observed != expected for observed, expected in bindings.values()):
        raise FirmwareError("Original camera constructor/vtable binding differs")

    rows = []
    for mode, flash_id, drive_id in ((0x2003, 0x15f, 0x153), (0x2006, 0x161, 0x159)):
        for service_context in (0, 6):
            for setting in range(8):
                settings = {flash_id: setting, drive_id: 0}
                probe = FlashProbe(code, mode=mode, settings=settings)
                rows.append({"mode": hex(mode), "service_context_byte": service_context,
                             "modelled_lens_type": 1, "modelled_settings": settings,
                             **probe.command(service_mode=service_context)})
    guards = []
    for label, inputs, extra in (("drive-3", {}, {0x159: 3}),
                                  ("dynamic-range-2", {}, {0x15e: 2}),
                                  ("lens-class-0", {"lens_type": 0}, {})):
        settings = {0x161: 4, **extra}
        probe = FlashProbe(code, mode=0x2006, settings=settings, **inputs)
        guards.append({"guard": label, "modelled_settings": settings, "modelled_inputs": inputs,
                       **probe.command(service_mode=6)})
    report = {
        "schema_version": 1,
        "status": "bounded-native-arm-parameter-writes-with-modelled-inputs",
        "original_firmware_sha256": baseline.FIRMWARE_SHA256,
        "decoded_section_1_sha256": sha256(code),
        "hardware_verified": False,
        "selfie_flash_fixed": False,
        "whole_camera_emulated": False,
        "flash_hardware_code_executed": False,
        "unlisted_setting_values_modelled_as": 0,
        "modelled_dependencies": ["RAM object allocation", "EEPROM settings",
                                  "lens classification", "camera state query", "logging"],
        "native_path": ["0x40442af4", "0x40347fe8", "0x40695360", "0x406c3ad0",
                        "0x4040afd8", "0x40345b48"],
        "stop_address": hex(FlashProbe.STOP_AFTER_WRITE),
        "writes": {"iq_enum": "0x419f0538 + 0x11 (byte)",
                   "system_parameter": "modelled parameter buffer + 0x1a * 4 (u32)"},
        "constructor_chain": ["startup call 0x40248ab4 -> 0x40267810",
                              "0x40267848: store main vtable 0x4076f5f0",
                              "0x40267854: store IQ table main+0x3a0+0x1f8 = 0x4076fb88",
                              "0x40248abc: store returned object through literal 0x40248c78"],
        "binding_checks": {key: {"observed": hex(value), "matches": value == expected}
                           for key, (value, expected) in bindings.items()},
        "cases": rows,
        "existing_guard_cases": guards,
        "ae_mode_mapping": {
            "native_path": ["0x404410e4", "0x40695040", "0x406c2bdc",
                            "0x40316ea8", "0x4031390c", "0x40316eb0"],
            "stop_address": "0x40316eb4",
            "cases": [AeModeProbe(code, mode=mode).mapping()
                      for mode in (0x2002, 0x2003, 0x2006, 0x200b, 0x200c, 0x200d)],
            "separate_3d_input_control": AeModeProbe(code, mode=0x2006).mapping(parameter_3d_mode=1),
            "diagnostic_names": {"0x80": "AEMode", "parameter_0x47": "GetCaptureMode",
                                 "parameter_0x72": "GetSmartProMode", "parameter_0x51": "Get3DMode"},
            "other_input_fields_modelled_as": 0,
            "exposure_calculated": False,
        },
        "static_runtime_flash_trace": {
            "evidence_kind": "static-disassembly-and-diagnostic-string-bindings",
            "executed_in_this_probe": False,
            "diagnostic": "0x401245b0 references iFlashEnable at 0x40124964",
            "query_selector": "0x2c",
            "query_path": ["0x40342494", "0x40336e90", "0x40314fd8",
                           "0x403152c4", "0x403137d8", "0x40313834"],
            "queried_field": "CAe output object returned by 0x4053b014, signed halfword +0x28",
            "writer": "0x4053b364 copies program-line output byte +9 into that halfword",
            "ordinary_selfshot_ae_5_policy": "0x4043f3b4 -> 0x4024ab3c -> 0x40249f34",
            "separate_ae_0x1c_policy": "0x4024c640 explicitly clears output byte +9",
            "caution": "This does not establish on-device runtime inputs or the physical flash veto. "
                       "0x40344160 is AdaptiveToneOn per diagnostic 0x403174f0, not flash availability.",
        },
        "conclusion": "The original setter can write nonzero flash parameters in self-shot mode "
                      "under these inputs. This does not prove physical flash operation or "
                      "exclude a later capture-stage restriction.",
    }
    write_json(args.output, report)
    print(json.dumps({"cases": len(rows), "guard_cases": len(guards),
                      "report": str(args.output), "hardware_verified": False}))


if __name__ == "__main__":
    main()
