"""Verify a candidate without calling the patch writer or touching a camera."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from nxmini import baseline, relc
from nxmini.firmware import Firmware
from nxmini.probe import (CaptureProbe, ManualApertureProbe, FlashMenuProbe,
                          FlashSelectionProbe, FlashProbe, FlashAeProbe, LensStartupProbe,
                          FlashEntryProbe, SmartPanelFlashProbe, CORE, OBJECT)
from nxmini.util import sha256, write_json


def verify_manual_aperture(code, edits):
    cases = []
    for label, selected in (("original", ()), ("candidate", edits)):
        probe = ManualApertureProbe(code, mode=0x2002, detached=True, edits=selected)
        initialized = probe.initialize_default()
        fallback = probe.aperture_table()
        expected_nodes = list(range(64, 145, 4)) if label == "original" else [64]
        if fallback["table_internal_values"] != expected_nodes:
            raise ValueError("Unexpected native fallback aperture table")
        if fallback["lens_fields"]["IrisDrive"] != 0 or fallback["lens_fields"]["LensReady"] != 0:
            raise ValueError("Fallback lens state differs")
        compensation = [probe.compensate(40, av) for av in (64, 68, 80, 96, 128, 144)]
        for result in compensation:
            if not result["tv_plus_av_preserved"] or result["output_internal"]["sv"] != 80:
                raise ValueError("Native AV compensation changed numeric exposure or SV")
            if label == "candidate" and result["output_internal"]["av"] != 64:
                raise ValueError("Candidate fallback did not hold AV constant")
        # A synthetic transport table enters the original cache loader. This is
        # a protocol boundary model, not an electrical or original 9mm test.
        loaded = probe.load_transport_table([56, 64, 72, 80])
        probe.write32(probe.PARAM_BUFFER + 0xa4, 56)
        received = probe.aperture_table()
        if received["table_internal_values"] != [56, 64, 72, 80] or received["lens_fields"]["LensReady"] != 1:
            raise ValueError("Received lens table did not replace the fallback")
        detached = probe.detach_callback()
        restored = probe.aperture_table()
        if restored["table_internal_values"] != expected_nodes:
            raise ValueError("Native detach callback did not restore the expected fallback")
        if probe.call(0x403f0e98, OBJECT)["result"] != 1:
            raise ValueError("Aperture change obscured the physical detached input")
        cases.append({"profile": label, "initialize": initialized, "fallback": fallback,
                      "compensation_cases": compensation, "synthetic_transport_load": loaded,
                      "loaded_table": received, "detach_callback": detached, "after_detach": restored})
    if cases[0]["loaded_table"] != cases[1]["loaded_table"]:
        raise ValueError("Synthetic received-data lens path changed")
    return {"status": "bounded-native-fallback-table-and-compensation-verified",
            "evidence": {"type": "CAeLensIF", "initialize": "0x40520fd4",
                         "detach_callback": "0x40521d94", "shared_defaults": "0x40520de8",
                         "edited_instruction": "0x40520ee4", "table_getter": "0x40521f8c",
                         "native_ae_copy_fragment": "0x403162f8..0x40316354",
                         "native_av_compensation": "0x40249ed0"},
            "cases": cases, "physical_aperture_known": False,
            "sensor_metering_emulated": False, "whole_ae_solver_executed": False,
            "hardware_verified": False, "smart_auto_exposure_verified": False}


def verify_flash_menu(code, edits):
    def word(address):
        offset = address - 0x40000000
        return int.from_bytes(code[offset:offset + 4], "little")

    # Constructor -> vtable -> RTTI verifies this is CMenuFlash, not another
    # setting with a similar numeric selector. Flash selector is decimal 10.
    bindings = {"constructor_table": (word(0x403ff620), 0x4076a87c),
                "table_rtti": (word(0x4076a878), 0x40762cc4),
                "rtti_name": (word(0x40762cc8), 0x40765696),
                "menu_availability_slot": (word(0x40780074 + 0x24), 0x403492b0),
                "menu_metadata_slot": (word(0x40780074 + 0x14), 0x40346d44)}
    if any(a != b for a, b in bindings.values()) or code[0x765696:0x7656a3] != b"10CMenuFlash\0":
        raise ValueError("CMenuFlash binding differs from the original")
    original, candidate = FlashMenuProbe(code), FlashMenuProbe(code, edits=edits)
    changed = []
    for mode in range(0x10000):
        first = original.options(mode, guarded=False)["rows"]
        second = candidate.options(mode, guarded=False)["rows"]
        if first != second:
            changed.append({"mode": hex(mode), "original": first, "candidate": second})
    if changed != [{"mode": "0x2006", "original": [0], "candidate": [0, 4, 5, 6, 7]}]:
        raise ValueError("Flash menu changed a different set of modes/options")
    guarded = []
    for mode in (0x2002, 0x2003, 0x2006, 0x200a, 0x200b, 0x200c, 0x200d, 0x2010):
        expected = ([0, 1] if mode == 0x2002 else [0, 2, 3] if mode == 0x2010 else
                    [0] if mode == 0x200a else [0, 4, 5, 6, 7])
        result = candidate.options(mode)
        if [r["value"] for r in result["rows"]] != expected:
            raise ValueError("Native guarded flash menu rows differ")
        guarded.append(result)
    guards = []
    for label, inputs, extra in (("drive-3", {}, {0x159: 3}),
                                  ("dynamic-range-2", {}, {0x15e: 2}),
                                  ("lens-class-0", {"lens_type": 0}, {})):
        settings = {0x161: 4, **extra}
        menu = FlashMenuProbe(code, edits=edits, settings=settings, **inputs).options(0x2006)
        cache = FlashProbe(code, mode=0x2006, edits=edits, settings=settings, **inputs).command(service_mode=6)
        if menu["rows"] or cache["iq_flash_enum"] or cache["system_parameter_0x1a"]:
            raise ValueError("C04 altered an existing flash guard")
        guards.append({"guard": label, "menu": menu, "caches": cache})
    caches = []
    for setting in (0, 4, 5, 6, 7):
        result = FlashProbe(code, mode=0x2006, edits=edits, settings={0x161: setting}).command(service_mode=6)
        if (setting == 0) != (result["system_parameter_0x1a"] == 0):
            raise ValueError("C04 flash parameter path did not retain selected values")
        caches.append({"modelled_setting": setting, **result})
    return {"status": "native-selfshot-option-dispatch-and-guards-verified",
            "binding_checks": {k: {"observed": hex(a), "matches": a == b} for k, (a, b) in bindings.items()},
            "entry": "0x403ff24c", "row_helper": "0x403ff11c", "flash_selector_decimal": 10,
            "edited_instruction": "0x403ff288", "modes_compared": 65536,
            "comparison_boundary": "original row-helper calls; row helper modelled as collector for mode sweep",
            "changed_modes": changed, "guarded_native_helper_cases": guarded,
            "retained_guard_cases": guards, "parameter_cache_cases": caches,
            "modelled_dependencies": ["UI row sink", "memcpy", "EEPROM", "lens classification", "zero shared UI counts", "logs"],
            "menu_input_to_eeprom_executed": False, "ui_rendered": False,
            "physical_flash_verified": False, "hardware_verified": False}


def verify_flash_selection(code, edits):
    def summarize(row):
        return {key: row[key] for key in ("ram_setting_changes", "native_setting_writes",
                                          "ram_table_dirty", "iq_flash_enum", "system_parameter_0x1a")}

    original = FlashSelectionProbe(code, mode=0x2006)
    original_rows = original.build_rows(0x2006)
    if [r["value"] for r in original_rows["rows"]] != [0]:
        raise ValueError("Original SelfShot row storage differs")
    cases = []
    for value, iq, system in ((0, 0, 0), (4, 3, 3), (5, 6, 4), (6, 7, 5), (7, 8, 6)):
        comparisons = []
        for mode, selected in ((0x2003, ()), (0x2003, edits), (0x2006, edits)):
            target, other = (0x161, 0x15f) if mode == 0x2006 else (0x15f, 0x161)
            probe = FlashSelectionProbe(code, mode=mode, edits=selected, settings={target: 0, other: 7})
            menu = probe.build_rows(mode)
            if [r["value"] for r in menu["rows"]] != [0, 4, 5, 6, 7]:
                raise ValueError("Native row storage differs from allowed flash options")
            committed = probe.select_value(value)
            expected_changes = [] if value == 0 else [{"id": target, "before": 0, "after": value}]
            expected_writes = [] if value == 0 else [{"id": target, "bytes": 1, "value": value}]
            if (committed["ram_setting_changes"] != expected_changes or
                    committed["native_setting_writes"] != expected_writes or
                    committed["ram_table_dirty"] != int(value != 0) or
                    committed["iq_flash_enum"] != iq or committed["system_parameter_0x1a"] != system):
                raise ValueError("Original selected-row commit wrote unexpected settings/cache values")
            reopened = probe.build_rows(mode)
            if reopened["selected_setting"] != value or reopened["selected_row"] != [0, 4, 5, 6, 7].index(value):
                raise ValueError("Native widget did not read back its selected value from RAM")
            cases.append({"mode": hex(mode), "candidate": bool(selected), "menu": menu,
                          "commit": committed, "reopen": reopened})
            if mode == 0x2003:
                comparisons.append(summarize(committed))
        if comparisons[0] != comparisons[1]:
            raise ValueError("Program selected-row commit behavior changed")
    # A clean RAM table already holding the selected value must remain clean.
    unchanged = FlashSelectionProbe(code, mode=0x2006, edits=edits, settings={0x161: 4, 0x15f: 7})
    unchanged.build_rows(0x2006)
    noop = unchanged.select_value(4)
    if noop["native_setting_writes"] or noop["ram_setting_changes"] or noop["ram_table_dirty"]:
        raise ValueError("Same-value selection dirtied/rewrote the RAM table")
    guards = []
    for label, inputs, settings in (("drive-3", {}, {0x159: 3}),
                                     ("dynamic-range-2", {}, {0x15e: 2}),
                                     ("lens-class-0", {"lens_type": 0}, {})):
        menu = FlashSelectionProbe(code, mode=0x2006, edits=edits, settings=settings, **inputs).build_rows(0x2006)
        if menu["rows"]:
            raise ValueError("Original row storage bypassed a flash availability guard")
        guards.append({"guard": label, "menu": menu})
    return {"status": "native-row-storage-setting-ram-write-and-flash-cache-path-verified",
            "original_selfshot": original_rows, "cases": cases,
            "same_value_clean_table": noop, "retained_guard_cases": guards,
            "native_path": ["0x403ff318", "0x403ff24c", "0x403ff11c", "0x403fe49c",
                            "0x403ff4b4", "0x403ff434", "0x40346dd0", "0x403570b0",
                            "0x40356da8", "0x40345eb0", "0x40442af4", "0x406c3ad0"],
            "ram_settings_descriptor": "0x4098f22c", "selfshot_setting": "0x161",
            "program_setting": "0x15f", "stop_address": "0x406c3b98",
            "modelled_dependencies": ["RAM allocation/initial settings table", "selected row index",
                                      "inactive popup state", "shared UI counts", "memcpy", "mutexes",
                                      "timing/logs", "lens classification", "camera state query"],
            "setting_getter_and_setter_modelled": False,
            "selected_row_to_ram_setting_commit_executed": True,
            "key_or_touch_event_dispatch_executed": False,
            "settings_nonvolatile_persistence_executed": False,
            "flash_hardware_code_executed": False, "hardware_verified": False}


def _ae_result_signature(result):
    return {key: result[key] for key in ("program_output_tv_av_sv_internal", "program_output_flags",
                                         "program_output_flash_enable", "runtime_iFlashEnable")}


def verify_native_flash_ae(code, edits):
    def word(address):
        return int.from_bytes(code[address - 0x40000000:address - 0x40000000 + 4], "little")
    bindings = {"info_singleton": (word(0x40313684), 0x40994a70),
                "program_singleton": (word(0x4043f32c), 0x40994ad0),
                "output_descriptor": (word(0x4053ba34), 0x40994a54)}
    if any(a != b for a, b in bindings.values()):
        raise ValueError("Native AE singleton bindings differ")
    cases = []
    for value in (0, 4, 5, 6, 7):
        reference = None
        for mode, selected in ((0x2003, ()), (0x2003, edits), (0x2006, edits)):
            probe = FlashAeProbe(code, mode=mode, edits=selected)
            menu = probe.build_rows(mode)
            committed = probe.select_value(value)
            prepared = probe.prepare_ae(lens_table=(56, 64, 72, 80))
            expected_mode = 4 if value == 0 else 1 if value in (4, 5) else 3
            if (prepared["program_ae_mode"] != (5 if mode == 0x2006 else 0) or
                    prepared["program_flash_mode"] != expected_mode or
                    prepared["program_flash_available"] != int(value != 0)):
                raise ValueError("Native flash-to-AE mode/availability conversion differs")
            results = [probe.evaluate_ae(v) for v in (0, 100, 200)]
            if any(r["runtime_iFlashEnable"] != int(value != 0) or
                   r["program_output_flash_enable"] != int(value != 0) for r in results):
                raise ValueError("Native program-line flash enable differs")
            if mode == 0x2003:
                signature = [_ae_result_signature(r) for r in results]
                if reference is None:
                    reference = signature
                elif reference != signature:
                    raise ValueError("Program AE numeric output changed for a received multi-node table")
            cases.append({"mode": hex(mode), "candidate": bool(selected), "selected_setting": value,
                          "menu": menu, "commit": committed, "prepared": prepared, "evaluations": results})
    strobe_guards = []
    for label, inputs, expected in (("external-not-genuine", {"strobe_in_ext": 1, "external_ready": 1}, 0),
                                     ("external-not-ready", {"strobe_in_ext": 1, "external_genuine": 1}, 0),
                                     ("external-ready", {"strobe_in_ext": 1, "external_genuine": 1, "external_ready": 1}, 1),
                                     ("external-charging-field-set", {"strobe_in_ext": 1, "external_genuine": 1, "charging": 1}, 1)):
        probe = FlashAeProbe(code, mode=0x2006, edits=edits)
        probe.build_rows(0x2006)
        probe.select_value(4)
        prepared = probe.prepare_ae(**inputs)
        result = probe.evaluate_ae(100)
        if prepared["program_flash_available"] != expected or result["runtime_iFlashEnable"] != expected:
            raise ValueError("Native strobe availability rule changed")
        strobe_guards.append({"label": label, "modelled_inputs": inputs, "prepared": prepared, "result": result})
    setting_guards = []
    for label, inputs, settings in (("drive-3", {}, {0x159: 3}),
                                     ("dynamic-range-2", {}, {0x15e: 2}),
                                     ("lens-class-0", {"lens_type": 0}, {})):
        probe = FlashAeProbe(code, mode=0x2006, edits=edits, settings={0x161: 4, **settings}, **inputs)
        cached = probe.command(service_mode=6)
        prepared = probe.prepare_ae()
        result = probe.evaluate_ae(100)
        if cached["system_parameter_0x1a"] or prepared["program_flash_available"] or result["runtime_iFlashEnable"]:
            raise ValueError("Existing setting/lens guard did not reach native AE")
        setting_guards.append({"label": label, "cached": cached, "prepared": prepared, "result": result})
    return {"status": "native-menu-setting-to-program-line-and-runtime-flash-flag-verified",
            "binding_checks": {k: {"observed": hex(a), "matches": a == b} for k, (a, b) in bindings.items()},
            "cases": cases, "strobe_guard_cases": strobe_guards, "setting_guard_cases": setting_guards,
            "native_path": ["0x40316cb4", "0x40316f18", "0x403160c8", "0x4043f3b4",
                            "0x4024ab3c", "0x40249f34", "0x4053d388", "0x4053b364"],
            "runtime_flag": "AE output object +0x28; original diagnostic iFlashEnable",
            "native_program_line_and_output_update_executed": True,
            "metering_sampled": False, "calibration_loaded": False,
            "flash_hardware_code_executed": False, "hardware_verified": False}


def verify_single_aperture_ae(code, edits):
    reference_edits = [e for e in edits if e[0] != 0x24aaf0]
    if len(reference_edits) != len(edits) - 1:
        raise ValueError("C05 single-aperture block is missing/duplicated")
    smart_auto = []
    brightness = (0, 64, 100, 160, 200, 256, 288, 320)
    for scene in (0, 1, 3, 6, 0xd, 0x12):
        pair = []
        for label, selected in (("C04", reference_edits), ("C05", edits)):
            probe = FlashAeProbe(code, mode=0x2002, detached=True, lens_type=0, edits=selected)
            cached = probe.command()
            prepared = probe.prepare_ae(lens_table=None, smart_auto_scene=scene)
            if (prepared["program_ae_mode"] != 0x15 or prepared["lens"]["table_internal_values"] != [64] or
                    prepared["lens"]["lens_fields"]["LensReady"] != 0):
                raise ValueError("Smart Auto did not use its detached single-node defaults")
            results = [probe.evaluate_ae(v) for v in brightness]
            if any(r["runtime_iFlashEnable"] for r in results):
                raise ValueError("Flash unexpectedly enabled in the detached Smart Auto model")
            if label == "C05" and any(r["program_output_tv_av_sv_internal"][1] != 64 for r in results):
                raise ValueError("C05 Smart Auto changed a single-node aperture")
            pair.append({"profile": label, "cached": cached, "prepared": prepared, "evaluations": results})
        if scene != 0x12:
            if [_ae_result_signature(r) for r in pair[0]["evaluations"]] != [_ae_result_signature(r) for r in pair[1]["evaluations"]]:
                raise ValueError("C05 changed an already constrained Smart Auto path")
        else:
            for a, b in zip(pair[0]["evaluations"], pair[1]["evaluations"]):
                av, bv = a["program_output_tv_av_sv_internal"], b["program_output_tv_av_sv_internal"]
                if av != [-24, 96, 91] or bv != [8, 64, 91] or sum(av[:2]) != sum(bv[:2]):
                    raise ValueError("C05 fixed preset did not preserve numeric exposure while holding aperture")
        smart_auto.append({"modelled_scene": scene, "profiles": pair})
    multi_node = []
    for table in ((56, 64, 72, 80), (56, 64, 72, 80, 88, 96, 104, 112)):
        for mode, scene in ((0x2003, 0), (0x200b, 0), (0x200c, 0), (0x200d, 0), (0x2006, 0), (0x2002, 0x12)):
            pair = []
            for label, selected in (("C04", reference_edits), ("C05", edits)):
                probe = FlashAeProbe(code, mode=mode, edits=selected)
                probe.command()
                prepared = probe.prepare_ae(lens_table=table, smart_auto_scene=scene)
                if prepared["lens"]["table_count"] != len(table):
                    raise ValueError("Synthetic received table differs")
                results = [probe.evaluate_ae(v) for v in (0, 100, 200)]
                pair.append({"profile": label, "prepared": prepared, "evaluations": results})
            if [_ae_result_signature(r) for r in pair[0]["evaluations"]] != [_ae_result_signature(r) for r in pair[1]["evaluations"]]:
                raise ValueError("C05 changed the native multi-node AE behavior")
            multi_node.append({"mode": hex(mode), "modelled_scene": scene,
                               "synthetic_received_table": table, "profiles": pair})
    return {"status": "native-smart-auto-fixed-aperture-and-multi-node-regression-verified",
            "smart_auto_scene_pairs": smart_auto, "multi_node_mode_pairs": multi_node,
            "brightness_inputs_internal": brightness, "native_parameter_builder": "0x403160c8",
            "native_program_dispatch": "0x4043f3b4", "native_runtime_output": "0x4053b364",
            "fixed_preset": "0x4024b38c -> 0x4024aa18", "edited_block": "0x4024aaf0..0x4024ab0b",
            "condition": "program aperture table count equals one, including received fixed-aperture tables",
            "scene_classifier_executed": False, "metering_sampled": False,
            "physical_aperture_known": False, "original_9mm_table_captured": False,
            "hardware_verified": False, "smart_auto_exposure_verified_on_camera": False}


def verify_lens_startup_notifications(code, edits):
    def word(address):
        return int.from_bytes(code[address - 0x40000000:address - 0x40000000 + 4], "little")
    bindings = {"callback_core_literal": (word(0x402681c8), CORE),
                "callback_app_literal": (word(0x4026827c), 0x4099019c),
                "app_cached_event_slot": (word(0x40773cdc + 0x18), 0x403efadc),
                "app_deferred_drain_slot": (word(0x40773cdc + 0x1c), 0x403f06ec),
                "attached_state_query_slot": (word(0x40773cdc + 0x474), 0x403f10c8)}
    if any(a != b for a, b in bindings.values()):
        raise ValueError("Lens startup callback bindings differ")
    modes = []
    for mode in (0x2002, 0x2003, 0x200b, 0x200c, 0x200d, 0x2006):
        pair = []
        for label, selected in (("original", ()), ("candidate", edits)):
            probe = LensStartupProbe(code, mode=mode, lens_type=0, edits=selected)
            callback = probe.callback(5)
            state = callback["after"]
            if (state["core_0"] & 1 or state["core_28"] & 0x14 != 0x14 or
                    state["app_38"] & 3 != 2 or callback["queued_events"]):
                raise ValueError("Native early detach callback/cache state differs")
            drained = probe.drain_deferred()
            expected = [{"boundary": "modelled-deferred-event-queue-api", "message": 0x4d,
                         "event": 5, "payload": 0}]
            if (drained["queued_events"] != expected or drained["execution"].get("boundary") or
                    drained["after"]["core_24"] & 0x18000000 or
                    drained["after"]["core_28"] & 0x14 != 0x14 or drained["after"]["core_0"] & 1):
                raise ValueError("Native deferred detach handling changed/lost physical state")
            route = probe.assume_ready_and_s2()
            expected_boundary = "lens-event-interception" if label == "original" else "mode-photo-dispatch"
            if route["route"].get("boundary") != expected_boundary:
                raise ValueError("S2 did not consume the callback-derived detached state as expected")
            pair.append({"profile": label, "early_callback": callback,
                         "deferred": drained, "after_modelled_readiness": route})
        if (pair[0]["early_callback"] != pair[1]["early_callback"] or
                pair[0]["deferred"] != pair[1]["deferred"]):
            raise ValueError("Candidate changed the original early notification logic")
        modes.append({"mode": hex(mode), "profiles": pair})
    # Separate controls preserve attached/ready flag transitions and late enqueue.
    probe = LensStartupProbe(code, edits=edits, detached=True)
    attached = probe.callback(6, ready_bit0=True, payload=0x1234)
    ready = probe.callback(7, ready_bit0=True, payload=0x5678)
    if (attached["after"]["core_28"] & 0x10 or attached["after"]["core_e4"] != 1 or
            ready["after"]["core_e4"] != 0 or
            [r["event"] for r in attached["queued_events"]] != [6] or
            [r["event"] for r in ready["queued_events"]] != [7]):
        raise ValueError("Native attach/ready callback transitions differ")
    statuses = []
    for raw, expected in ((-1, 0x20000018), (-2, 0x2000001b), (-3, 0x2000001c),
                            (9, 0x2000001a), (16, 0x2000001a), (0, 0x20000019)):
        original = LensStartupProbe(code).module_status(raw)
        candidate = LensStartupProbe(code, edits=edits).module_status(raw)
        if original != candidate or candidate["execution"]["result"] != expected:
            raise ValueError("Native module status classification changed")
        statuses.append(candidate)
    return {"status": "native-lens-callback-cache-and-deferred-notifications-verified",
            "binding_checks": {k: {"observed": hex(a), "matches": a == b} for k, (a, b) in bindings.items()},
            "mode_cases": modes, "late_attached_control": attached, "late_ready_control": ready,
            "independent_module_status_cases": statuses,
            "native_path": ["0x40267f60", "0x403efadc", "0x403f06ec"],
            "queue_boundary": "0x403ee4a4 (modelled enqueue API)",
            "boot_completed_caller_static": "0x4050035c invokes app slot +0x1c when core+0x24 bit 27 is set",
            "module_status_to_callback_conversion_executed": False,
            "queue_delivery_executed": False, "sensor_ready_transition_executed": False,
            "no_lens_class_modelled_as": 0,
            "s2_ready_state_modelled": True, "complete_cold_boot_emulated": False,
            "hardware_verified": False, "no_contact_short_cold_boot_verified": False}


def verify_selfshot_entries(code, edits):
    def word(address):
        return int.from_bytes(code[address - 0x40000000:address - 0x40000000 + 4], "little")

    bindings = {"menu_iterator_slot": (word(0x4076cc98 + 0x8c), 0x40535d34),
                "original_selfshot_table": (word(0x40535ee4), 0x40804a34),
                "adjacent_program_flash_selector": (word(0x40804a30), 10),
                "fn_registry_literal": (word(0x40509410), 0x40991324),
                "fn_flash_constructor_vtable": (word(0x4056a724), 0x40773b90),
                "fn_flash_registry_store": (word(0x4053a524), 0xe5840020),
                "fn_flash_init_slot": (word(0x40773b90 + 8), 0x406f917c),
                "fn_flash_enable_slot": (word(0x40773b90 + 0x10), 0x406f9384),
                "fn_flash_next_value_slot": (word(0x40773b90 + 0x18), 0x406f9508)}
    if any(a != b for a, b in bindings.values()):
        raise ValueError("SelfShot outer MENU/Fn binding differs")
    menu_cases, panel_cases = [], []
    for mode in (0x2002, 0x2003, 0x2006, 0x200b, 0x200c, 0x200d):
        original = FlashEntryProbe(code, mode=mode).outer_options(mode)
        candidate = FlashEntryProbe(code, mode=mode, edits=edits).outer_options(mode)
        expected = ([{"selector": 10, "disabled": False}] + original["rows"]
                    if mode == 0x2006 else original["rows"])
        if candidate["rows"] != expected:
            raise ValueError("C06 changed unexpected outer MENU items")
        menu_cases.append({"original": original, "candidate": candidate})
        original = SmartPanelFlashProbe(code, mode=mode).fn_layout(mode)
        candidate = SmartPanelFlashProbe(code, mode=mode, edits=edits).fn_layout(mode)
        if mode == 0x2006:
            if (original["flash_present"] or candidate["grid_registry_indices"][:3] !=
                    original["grid_registry_indices"][:3] or
                    candidate["grid_registry_indices"][3:] != [[8] * 4, [None] * 4] or
                    candidate["pane_fields"] != [2, 2, 4] or
                    candidate["flash_grid_cell_count"] != 4 or
                    candidate["flash_values"] != [0, 4, 5, 6, 7] or
                    not candidate["flash_selectable"]):
                raise ValueError("C06 Fn layout lost existing cells or failed to add flash")
        elif original != candidate:
            raise ValueError("C06 changed another mode's Fn layout")
        panel_cases.append({"original": original, "candidate": candidate})

    original, candidate = SmartPanelFlashProbe(code), SmartPanelFlashProbe(code, edits=edits)
    changed = []
    for mode in range(0x10000):
        first = original.fn_values(mode, guarded=False)["values"]
        second = candidate.fn_values(mode, guarded=False)["values"]
        if first != second:
            changed.append({"mode": hex(mode), "original": first, "candidate": second})
    if changed != [{"mode": "0x2006", "original": [0], "candidate": [0, 4, 5, 6, 7]}]:
        raise ValueError("C06 Fn options changed an unexpected set of modes")
    guards = []
    for label, inputs in (("drive-3", {"settings": {0x159: 3}}),
                           ("dynamic-range-2", {"settings": {0x15e: 2}}),
                           ("detached-lens-class-0", {"detached": True, "lens_type": 0})):
        for flip_flag in (0, 0x4000):
            probe = SmartPanelFlashProbe(code, mode=0x2006, edits=edits, **inputs)
            probe.write32(CORE + 0x10, flip_flag)
            menu = probe.outer_options(0x2006)
            panel = probe.fn_layout(0x2006)
            flash_row = next(r for r in menu["rows"] if r["selector"] == 10)
            if not flash_row["disabled"] or panel["flash_selectable"] or panel["flash_values"]:
                raise ValueError("C06 bypassed an original outer flash guard")
            guards.append({"guard": label, "core_plus_0x10": hex(flip_flag), "menu": menu, "fn": panel})
    probe = SmartPanelFlashProbe(code, mode=0x2006, edits=edits, settings={0x15f: 7})
    probe.fn_layout(0x2006)
    next_value_cases, before = [], 0
    for value, iq, system in ((4, 3, 3), (5, 6, 4), (6, 7, 5), (7, 8, 6), (0, 0, 0)):
        result = probe.fn_next_value()
        if (result["selected_setting"] != value or
                result["ram_setting_changes"] != [{"id": 0x161, "before": before, "after": value}] or
                result["native_setting_writes"] != [{"id": 0x161, "bytes": 1, "value": value}] or
                (result["iq_flash_enum"], result["system_parameter_0x1a"]) != (iq, system)):
            raise ValueError("C06 Fn next-value callback changed unexpected settings or flash caches")
        next_value_cases.append(result)
        before = value
    return {"status": "native-outer-menu-fn-grid-option-storage-and-next-value-path-verified",
            "binding_checks": {k: {"observed": hex(a), "matches": a == b} for k, (a, b) in bindings.items()},
            "menu_iterator_cases": menu_cases, "fn_mode_dispatch_cases": panel_cases,
            "fn_mode_values_compared": 65536, "fn_changed_modes": changed,
            "retained_guard_cases": guards, "fn_next_value_cases": next_value_cases,
            "menu_iterator": "0x40535d34", "fn_dispatch": "0x40509bd0",
            "fn_selfshot_layout": "0x40509460", "fn_native_grid_store": "0x40286990",
            "fn_flash_values": "0x406f917c", "fn_flash_next_value": "0x406f9508",
            "models": ["object allocation", "unrelated MENU item availability/item sink",
                       "unrelated Fn item init/selection", "flash rectangle setup/list reset",
                       "Fn title/drawing", "existing settings/transport/logging dependencies"],
            "native_grid_and_flash_value_storage_executed": True,
            "native_fn_next_value_setting_and_flash_cache_write_executed": True,
            "ui_rendered": False, "physical_flip_or_key_event_executed": False,
            "physical_flash_verified": False, "hardware_verified": False}


def verify(original_path, candidate_path, manifest_path):
    manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
    candidate_id = manifest.get("candidate_id", "C01")
    if candidate_id not in ("C01", "C02", "C03", "C04", "C05", "C06"):
        raise ValueError("Unknown candidate probe profile")
    original, candidate = Firmware.read(original_path), Firmware.read(candidate_path)
    if (manifest["input_sha256"] != baseline.FIRMWARE_SHA256 or
            sha256(original.data) != baseline.FIRMWARE_SHA256 or
            sha256(candidate.data) != manifest["output_sha256"]):
        raise ValueError("Candidate/original/manifest SHA-256 mismatch")
    if len(original.data) != len(candidate.data) or original.header != candidate.header:
        raise ValueError("Container header or file length unexpectedly changed")
    if original.resources != candidate.resources or len(original.sections) != len(candidate.sections):
        raise ValueError("Resource or section layout changed")
    operations = manifest["operations"]
    if not operations or any(o["space"] != "section" for o in operations):
        raise ValueError("This verifier supports decoded section patches only")
    sections, decoded_original, edits, allowed = [], {}, [], []
    for i, (a, b) in enumerate(zip(original.sections, candidate.sections)):
        if a.number != b.number or a.offset != b.offset:
            raise ValueError("Section order/offset changed")
        first = relc.decode(original.section_bytes(a))
        decoded_original[a.number] = first
        expected = bytearray(first)
        selected = [o for o in operations if o["section"] == a.number]
        for op in selected:
            offset = op["offset"]
            before, after = bytes.fromhex(op["before"]), bytes.fromhex(op["after"])
            if len(before) != len(after) or first[offset:offset + len(before)] != before:
                raise ValueError("Operation preimage/length mismatch")
            if a.number == 1 and int(op["analysis_address"], 16) != 0x40000000 + offset:
                raise ValueError("Operation address differs from decoded offset")
            expected[offset:offset + len(after)] = after
            if a.number == 1:
                edits.append((offset, before, after))
        second = relc.decode(candidate.section_bytes(b))
        if second != expected:
            raise ValueError(f"Unexpected decoded bytes in section {a.number}")
        if selected:
            capacity = (a.offset + a.size + 511) // 512 * 512
            if b.offset + b.size > capacity:
                raise ValueError("Patched section exceeded its original allocation")
            allowed += [(a.offset, capacity), (0x1ba + i * 8, 0x1be + i * 8)]
        elif original.section_bytes(a) != candidate.section_bytes(b):
            raise ValueError("An unselected compressed section changed")
        sections.append({"section": a.number, "decoded_bytes": len(second),
                         "original_encoded_bytes": a.size, "candidate_encoded_bytes": b.size,
                         "original_sha256": sha256(first), "candidate_sha256": sha256(second),
                         "exactly_matches_manifest": True})
    if any(o["section"] not in decoded_original for o in operations):
        raise ValueError("Operation refers to a missing section")
    # Account for every other container byte, including writer/resources/footer.
    code_end = original.header["code_offset"] + original.header["code_size"]
    allowed += [(code_end - 2, code_end), (original.footer_offset, original.footer_offset + 4)]
    position = 0
    for start, end in sorted(allowed):
        if start < position:
            raise ValueError("Overlapping allowed mutation regions")
        if original.data[position:start] != candidate.data[position:start]:
            raise ValueError(f"Unexpected container change before {start:#x}")
        position = end
    if original.data[position:] != candidate.data[position:]:
        raise ValueError("Unexpected final container change")
    layout = candidate.report()
    if not layout["valid"]:
        raise ValueError("Candidate known checksums/footer validation failed")

    code, probes = decoded_original[1], []
    mode_tables = [0x40770f10, 0x4076abd4, 0x4077452c, 0x407728d4]
    for table in mode_tables:
        offset = table - 0x40000000
        if (int.from_bytes(code[offset + 0x38:offset + 0x3c], "little") != 0x4027bde0 or
                int.from_bytes(code[offset + 0xe8:offset + 0xec], "little") != 0x4014eb60):
            raise ValueError("Original mode callback binding differs")
    name_address = int.from_bytes(code[0x14ee18:0x14ee1c], "little")
    if name_address != 0x40739571 or code[0x739571:0x73957d] != b"startS2Mode\0":
        raise ValueError("S2 callback log-name evidence differs")
    if (int.from_bytes(code[0x14fa18:0x14fa1c], "little") != 0x407395b6 or
            code[0x7395b6:0x7395c5] != b"doPhotoCapture\0"):
        raise ValueError("Common photo dispatch log-name evidence differs")
    through_start = candidate_id in ("C02", "C03", "C04", "C05", "C06")
    for state4 in ((0x80000, 0x480000) if through_start else (0x80000,)):
        for mode in (0x2002, 0x2003, 0x200b, 0x200c, 0x200d, 0x2006):
            for detached in (False, True):
                first = CaptureProbe(code, mode=mode, detached=detached).s2_route(
                    state4=state4, through_start=through_start)
                second = CaptureProbe(code, mode=mode, detached=detached, edits=edits).s2_route(
                    state4=state4, through_start=through_start)
                expected = ("mode-photo-dispatch" if through_start else "mode-start-s2") if detached else first.get("boundary")
                if second.get("boundary") != expected:
                    raise ValueError("Candidate S2 probe did not reach its expected boundary")
                if not detached and first != second:
                    raise ValueError("Attached lens probe changed")
                probes.append({"mode": hex(mode), "state4": hex(state4), "detached": detached,
                               "original": first, "candidate": second})
    events = []
    for event in (2, 6):
        first = CaptureProbe(code, detached=True).lens_event(event)
        debug = CaptureProbe(code, detached=True, nolens_flag=True).lens_event(event)
        patched = CaptureProbe(code, detached=True, edits=edits)
        second = patched.lens_event(event)
        if second != debug or second["result"] != 0:
            raise ValueError("Candidate differs from original nolens-enabled event path")
        if patched.call(0x403f0e98, OBJECT)["result"] != 1:
            raise ValueError("Candidate obscured physical detached state")
        events.append({"event": event, "original": first, "original_nolens_on": debug,
                       "candidate": second})
    aperture = verify_manual_aperture(code, edits) if candidate_id in ("C03", "C04", "C05", "C06") else None
    flash_menu = verify_flash_menu(code, edits) if candidate_id in ("C04", "C05", "C06") else None
    flash_selection = verify_flash_selection(code, edits) if candidate_id in ("C04", "C05", "C06") else None
    native_flash_ae = verify_native_flash_ae(code, edits) if candidate_id in ("C05", "C06") else None
    single_aperture_ae = verify_single_aperture_ae(code, edits) if candidate_id in ("C05", "C06") else None
    lens_startup = verify_lens_startup_notifications(code, edits) if candidate_id in ("C05", "C06") else None
    selfshot_entries = verify_selfshot_entries(code, edits) if candidate_id == "C06" else None
    return {
        "schema_version": 1, "candidate": candidate_id, "status": "offline-candidate-verified",
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_sha256": sha256(original.data), "output_sha256": sha256(candidate.data),
        "bytes": len(candidate.data), "operations": operations, "sections": sections,
        "all_resources_unchanged": len(candidate.resources), "writer_unchanged": True,
        "unaccounted_container_changes": False, "checksums": layout["checksums"],
        "s2_routes": probes, "detached_events": events,
        **({"manual_aperture_constraint": aperture} if aperture is not None else {}),
        **({"selfshot_flash_menu": flash_menu} if flash_menu is not None else {}),
        **({"selfshot_flash_selected_row": flash_selection} if flash_selection is not None else {}),
        **({"native_flash_ae": native_flash_ae} if native_flash_ae is not None else {}),
        **({"native_single_aperture_ae": single_aperture_ae} if single_aperture_ae is not None else {}),
        **({"native_lens_startup_notifications": lens_startup} if lens_startup is not None else {}),
        **({"selfshot_outer_entries": selfshot_entries} if selfshot_entries is not None else {}),
        "callback_evidence": {"entry": "0x4014eb60", "name": "startS2Mode",
                              "name_pointer_literal": "0x4014ee18", "name_address": hex(name_address),
                              "mode_vtables": [hex(t) for t in mode_tables]},
        "photo_dispatch_evidence": {"vtable_slot": "0xf0",
                                    "bindings": {hex(t): hex(int.from_bytes(code[t - 0x40000000 + 0xf0:t - 0x40000000 + 0xf4], "little")) for t in mode_tables},
                                    "common_entry": "0x4014f51c", "common_name": "doPhotoCapture",
                                    "common_name_literal": "0x4014fa18", "common_name_address": "0x407395b6",
                                    "reached_by_probe": through_start},
        "probe_ram": {"core_plus_4": "0x80000", "core_plus_8_before_s2": "0xc", "core_plus_0x330": 1, "app_state": 0},
        "mode_readiness": "Original 0x4027bde0 executes via original Program/ASM/SmartAuto/SelfShot vtables; camera slot 0x34 and timer state modelled as zero.",
        "probe_limits": "Synthetic RAM and explicit camera/timer/logging dependencies. C01 stops at startS2Mode; later candidates continue to original mode +0xf0 dispatch. C03 onward execute native default lens table/AE copy/AV compensation; C04 onward include menu dispatch and selected-row-to-settings-RAM checks. C05 additionally executes original parameter construction, complete selected program lines, interpolation and 0x4053b364 AE output update, observing software iFlashEnable. Its startup-notification probe executes the original callback/cache/deferred logic, then explicitly models sensor/system readiness before S2; event queues are sinks, not REALOS scheduling. Brightness, scene IDs, strobe fields, initial RAM, selected row, lens transport, scene value, libc memory functions, mutexes, timing/logs and camera state remain inputs/models. No complete cold boot, event delivery, key/touch dispatch, nonvolatile persistence, physical capture, scene classification, sensor metering, actual calibration or REALOS/ISP/flash hardware emulation.",
        "hardware_verified": False, "recovery_verified": False, "flash_ready": False,
        "no_contact_short_capture_fixed_on_camera": False,
        "smart_auto_exposure_verified": False, "selfshot_flash_fixed": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", type=Path, default=Path("data/firmware/DATANXmini.bin"))
    parser.add_argument("--candidate", type=Path,
                        default=Path("dist/nxmini-evo-C05/manual-lens-C05_OFFLINE_ONLY.bin"))
    parser.add_argument("--manifest", type=Path, default=Path("patches/manual-lens-c05.json"))
    parser.add_argument("--output", type=Path, default=Path("research/generated/manual-lens-c05.json"))
    args = parser.parse_args()
    report = verify(args.original, args.candidate, args.manifest)
    write_json(args.output, report)
    print(json.dumps({"candidate": report["candidate"], "output_sha256": report["output_sha256"],
                      "sections_verified": len(report["sections"]),
                      "resources_unchanged": report["all_resources_unchanged"],
                      "s2_routes": len(report["s2_routes"]), "flash_ready": False}))


if __name__ == "__main__":
    main()
