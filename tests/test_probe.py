import json
import faulthandler
import sys
from pathlib import Path

import pytest

pytest.importorskip("unicorn")

from nxmini.probe import (ArmProbe, CaptureProbe, FlashProbe, AeModeProbe,
                          ManualApertureProbe, FlashSelectionProbe, FlashAeProbe, LensStartupProbe,
                          FlashEntryProbe, SmartPanelFlashProbe,
                          CORE, OBJECT, eeprom_catalog)
from nxmini.errors import FirmwareError


@pytest.fixture(autouse=True)
def windows_unicorn_exception_handler():
    # Unicorn uses a Windows exception handler during memory mapping. Python's
    # faulthandler can print it as fatal even when Unicorn handles it and the
    # process continues (upstream unicorn issue #2264). Scope the change to these
    # native probes; a real process failure still fails pytest.
    enabled = sys.platform == "win32" and faulthandler.is_enabled()
    if enabled:
        faulthandler.disable()
    yield
    if enabled:
        faulthandler.enable()


@pytest.fixture(scope="module")
def code():
    path = Path("work/nxmini-1.10-verified/decoded/chunk-01.bin")
    if not path.exists():
        path = Path("work/nxmini-1.10/decoded/chunk-01.bin")
    if not path.exists():
        pytest.skip("Extract the official firmware to run native ARM probes")
    return path.read_bytes()


@pytest.fixture(scope="module")
def c06_edits():
    manifest = json.loads(Path("patches/manual-lens-c06.json").read_text())
    return [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
            for o in manifest["operations"]]


@pytest.mark.firmware
def test_c06_opens_outer_menu_before_inner_flash_widget(code, c06_edits):
    stock = FlashEntryProbe(code, mode=0x2006).outer_options(0x2006)
    updated = FlashEntryProbe(code, mode=0x2006, edits=c06_edits).outer_options(0x2006)
    assert not any(r["selector"] == 10 for r in stock["rows"])
    assert updated["rows"] == [{"selector": 10, "disabled": False}] + stock["rows"]
    assert updated["native_flash_availability_executed"]
    assert not updated["hardware_verified"]


@pytest.mark.firmware
def test_c06_fn_preserves_all_existing_cells_and_callee_saved_registers(code, c06_edits):
    from unicorn.arm_const import (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6,
                                    UC_ARM_REG_R7, UC_ARM_REG_R8, UC_ARM_REG_R9,
                                    UC_ARM_REG_R10, UC_ARM_REG_R11, UC_ARM_REG_SP)
    stock = SmartPanelFlashProbe(code, mode=0x2006).fn_layout(0x2006)
    probe = SmartPanelFlashProbe(code, mode=0x2006, edits=c06_edits)
    registers = (UC_ARM_REG_R4, UC_ARM_REG_R5, UC_ARM_REG_R6, UC_ARM_REG_R7,
                 UC_ARM_REG_R8, UC_ARM_REG_R9, UC_ARM_REG_R10, UC_ARM_REG_R11)
    sentinels = {reg: 0x60100000 + i * 0x101 for i, reg in enumerate(registers)}
    for reg, value in sentinels.items():
        probe.uc.reg_write(reg, value)
    result = probe.fn_layout(0x2006)
    assert result["grid_registry_indices"][:3] == stock["grid_registry_indices"][:3]
    assert result["grid_registry_indices"][3:] == [[8, 8, 8, 8], [None] * 4]
    assert result["pane_fields"] == [2, 2, 4]
    assert result["flash_grid_cell_count"] == 4
    assert result["flash_values"] == [0, 4, 5, 6, 7]
    assert result["flash_selectable"]
    assert {reg: probe.uc.reg_read(reg) for reg in registers} == sentinels
    assert probe.uc.reg_read(UC_ARM_REG_SP) == 0x5001f000
    assert not result["ui_rendered"]


@pytest.mark.firmware
@pytest.mark.parametrize("mode", [0x2002, 0x2003, 0x200b, 0x200c, 0x200d])
def test_c06_keeps_other_modes_outer_menu_and_fn_layout(code, c06_edits, mode):
    for cls, method in ((FlashEntryProbe, "outer_options"), (SmartPanelFlashProbe, "fn_layout")):
        original = getattr(cls(code, mode=mode), method)(mode)
        candidate = getattr(cls(code, mode=mode, edits=c06_edits), method)(mode)
        assert original == candidate


@pytest.mark.firmware
@pytest.mark.parametrize("flip_flag", [0, 0x4000])
@pytest.mark.parametrize("inputs", [{"settings": {0x159: 3}}, {"settings": {0x15e: 2}},
                                     {"detached": True, "lens_type": 0}])
def test_c06_keeps_native_flash_guards_at_both_outer_entries(code, c06_edits, flip_flag, inputs):
    probe = SmartPanelFlashProbe(code, mode=0x2006, edits=c06_edits, **inputs)
    probe.write32(CORE + 0x10, flip_flag)
    menu = probe.outer_options(0x2006)
    assert next(r for r in menu["rows"] if r["selector"] == 10)["disabled"]
    panel = probe.fn_layout(0x2006)
    assert panel["flash_present"] and not panel["flash_selectable"]
    assert panel["flash_values"] == []
    with pytest.raises(FirmwareError, match="no available"):
        probe.fn_next_value()


@pytest.mark.firmware
def test_c06_fn_native_next_value_writes_only_selfshot_setting_and_flash_cache(code, c06_edits):
    probe = SmartPanelFlashProbe(code, mode=0x2006, edits=c06_edits, settings={0x15f: 7})
    probe.fn_layout(0x2006)
    before = 0
    for value, iq, system in ((4, 3, 3), (5, 6, 4), (6, 7, 5), (7, 8, 6), (0, 0, 0)):
        result = probe.fn_next_value()
        assert result["selected_setting"] == value
        assert result["ram_setting_changes"] == [{"id": 0x161, "before": before, "after": value}]
        assert result["native_setting_writes"] == [{"id": 0x161, "bytes": 1, "value": value}]
        assert (result["iq_flash_enum"], result["system_parameter_0x1a"]) == (iq, system)
        assert not result["hardware_verified"]
        before = value


@pytest.mark.firmware
def test_setting_descriptions_and_native_mode_dispatch(code):
    source = Path("work/nxmini-1.10-verified/decoded/chunk-04.bin")
    if not source.exists():
        source = Path("work/nxmini-1.10/decoded/chunk-04.bin")
    names = eeprom_catalog(source.read_bytes())
    by_id = {row["id"]: row["name"] for row in names}
    assert len(names) == 207 and len(by_id) == 206
    assert sum(row["name"] == "EEP_DEPTH_BRACKET" for row in names) == 2
    assert by_id[0x15f] == "EEP_FLASH"
    for mode, drive in ((0x2003, 0x153), (0x2006, 0x159)):
        probe = ArmProbe(code, mode=mode)
        assert int.from_bytes(probe.uc.mem_read(CORE + 0x2a, 2), "little") == mode
        assert probe.call(0x403eea24, OBJECT, 9)["result"] == drive
    assert ArmProbe(code, mode=0x2006).call(0x403eea24, OBJECT, 10)["result"] == 0x161


@pytest.mark.firmware
@pytest.mark.parametrize("lens_type,flag,expected", [(1, False, 1), (0, False, 0), (0, True, 1), (2, False, 0), (2, True, 1)])
def test_original_flash_lens_qualification(code, lens_type, flag, expected):
    result = ArmProbe(code, lens_type=lens_type, nolens_flag=flag).call(0x406d4324, OBJECT)
    assert result["result"] == expected
    assert not result["hardware_verified"]


@pytest.mark.firmware
@pytest.mark.parametrize("drive,hdr,expected", [(0, 0, 4), (3, 0, 0), (0, 2, 0)])
def test_experimental_instruction_retains_other_flash_guards(code, drive, hdr, expected):
    manifest = json.loads(Path("experiments/non-xm-flash-qualification.json").read_text())
    op = manifest["operations"][0]
    edits = [(op["offset"], bytes.fromhex(op["before"]), bytes.fromhex(op["after"]))]
    settings = {0x161: 4, 0x159: drive, 0x15e: hdr}
    original = ArmProbe(code, mode=0x2006, detached=True, lens_type=0, settings=settings)
    modified = ArmProbe(code, mode=0x2006, detached=True, lens_type=0, settings=settings, edits=edits)
    assert original.call(0x40347fe8, OBJECT)["result"] == 0
    assert modified.call(0x40347fe8, OBJECT)["result"] == expected
    # The experiment does not change physical lens-detached reporting.
    assert modified.call(0x403f0e98, OBJECT)["result"] == 1


@pytest.fixture(scope="module")
def manual_edits():
    manifest = json.loads(Path("patches/manual-lens-c01.json").read_text())
    return [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
            for o in manifest["operations"]]


@pytest.mark.firmware
@pytest.mark.parametrize("mode", [0x2002, 0x2003, 0x200b, 0x200c, 0x200d, 0x2006])
def test_c01_routes_detached_s2_to_capture_boundary(code, manual_edits, mode):
    original = CaptureProbe(code, detached=True, mode=mode)
    candidate = CaptureProbe(code, detached=True, mode=mode, edits=manual_edits)
    assert original.s2_route()["boundary"] == "lens-event-interception"
    result = candidate.s2_route()
    assert result["boundary"] == "mode-start-s2"
    assert result["stopped_at"] == "0x4014eb60"
    assert result["result"] is None  # Stopped before the original startS2Mode callback.
    assert not result["hardware_verified"]
    assert candidate.call(0x403f0e98, OBJECT)["result"] == 1


@pytest.mark.firmware
@pytest.mark.parametrize("event", [2, 6])
def test_c01_detached_events_follow_original_nolens_policy(code, manual_edits, event):
    original = CaptureProbe(code, detached=True)
    debug_enabled = CaptureProbe(code, detached=True, nolens_flag=True)
    candidate = CaptureProbe(code, detached=True, edits=manual_edits)
    assert original.lens_event(event)["boundary"] == f"detached-event-{event}-popup-path"
    assert candidate.lens_event(event) == debug_enabled.lens_event(event)
    assert candidate.call(0x403f0e98, OBJECT)["result"] == 1


@pytest.mark.firmware
@pytest.mark.parametrize("mode", [0x2002, 0x2003, 0x200b, 0x200c, 0x200d, 0x2006])
def test_c01_attached_lens_routes_match_original(code, manual_edits, mode):
    original = CaptureProbe(code, mode=mode)
    candidate = CaptureProbe(code, mode=mode, edits=manual_edits)
    assert original.s2_route() == candidate.s2_route()


@pytest.mark.firmware
@pytest.mark.parametrize("fault", ["shared-lens-fault", "object-lens-fault"])
def test_c01_preserves_attached_lens_fault_interception(code, manual_edits, fault):
    for edits in ((), manual_edits):
        probe = CaptureProbe(code, edits=edits)
        if fault == "shared-lens-fault":
            probe.uc.mem_write(CORE + 0xe5, b"\x01")
        else:
            probe.write32(OBJECT + 0x38, 0x100)
        assert probe.s2_route()["boundary"] == "lens-event-interception"


@pytest.mark.firmware
@pytest.mark.parametrize("inputs", [{"state4": 0}, {"state8": 0x800}, {"app_state": 1}])
def test_c01_keeps_nonready_s2_from_capture_api(code, manual_edits, inputs):
    probe = CaptureProbe(code, detached=True, edits=manual_edits)
    result = probe.s2_route(**inputs)
    assert "boundary" not in result
    assert result["result"] == 1


@pytest.mark.firmware
def test_c01_executes_original_readiness_callback_before_photo_boundary(code, manual_edits):
    unready = CaptureProbe(code, mode=0x2002, detached=True, edits=manual_edits).s2_route(state8=0)
    assert unready["boundary"] == "mode-change-to-s1"
    ready = CaptureProbe(code, mode=0x2002, detached=True, edits=manual_edits).s2_route(state8=0xc)
    assert ready["boundary"] == "mode-start-s2"
    assert any(d["kind"] == "modelled-camera-slot-0x34" for d in ready["dependencies"])


@pytest.fixture(scope="module")
def c02_edits():
    manifest = json.loads(Path("patches/manual-lens-c02.json").read_text())
    return [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
            for o in manifest["operations"]]


@pytest.mark.firmware
@pytest.mark.parametrize("mode", [0x2002, 0x2003, 0x200b, 0x200c, 0x200d, 0x2006])
@pytest.mark.parametrize("state4", [0x80000, 0x480000])
def test_c02_passes_native_readiness_and_start_s2_to_original_photo_dispatch(code, c02_edits, mode, state4):
    candidate = CaptureProbe(code, detached=True, mode=mode, edits=c02_edits)
    result = candidate.s2_route(state4=state4, through_start=True)
    assert result["boundary"] == "mode-photo-dispatch"
    assert result["stopped_at"] == hex(candidate.photo_dispatch)
    assert candidate.call(0x403f0e98, OBJECT)["result"] == 1
    # The native timer selection/setter ran, with synthetic zero-valued EEPROM.
    assert any(d.get("address") == "0x500020a4" and d["argument1"] == 0
               for d in result["dependencies"])


@pytest.mark.firmware
def test_c02_fixes_c01_readiness_state_hole(code, manual_edits, c02_edits):
    c01 = CaptureProbe(code, detached=True, mode=0x2002, edits=manual_edits)
    c02 = CaptureProbe(code, detached=True, mode=0x2002, edits=c02_edits)
    assert c01.s2_route(state4=0x480000)["boundary"] == "mode-change-to-s1"
    assert c02.s2_route(state4=0x480000, through_start=True)["stopped_at"] == "0x4014f51c"


@pytest.mark.firmware
def test_c02_retains_inner_attached_lens_fault_rejection(code, c02_edits):
    for edits in ((), c02_edits):
        probe = CaptureProbe(code, detached=False, edits=edits)
        probe.write32(CORE + 4, 0x480000)
        probe.uc.mem_write(CORE + 0xe5, b"\x01")
        result = probe.call(0x4027bde0, probe.MODE_OBJECT)
        assert result["result"] == 0
        assert any(d["kind"] == "modelled-mode-cancel" for d in result["dependencies"])


@pytest.mark.firmware
@pytest.mark.parametrize("mode,flash_id,drive_id", [(0x2003, 0x15f, 0x153), (0x2006, 0x161, 0x159)])
@pytest.mark.parametrize("setting,iq_enum,parameter", [(0, 0, 0), (1, 1, 1), (2, 1, 1),
                                                     (3, 2, 2), (4, 3, 3), (5, 6, 4),
                                                     (6, 7, 5), (7, 8, 6)])
def test_original_derived_flash_setter_writes_both_native_caches(
        code, mode, flash_id, drive_id, setting, iq_enum, parameter):
    probe = FlashProbe(code, mode=mode, settings={flash_id: setting, drive_id: 0})
    result = probe.command(service_mode=6)
    assert result["boundary"] == "flash-parameter-caches-written"
    assert result["stopped_at"] == "0x406c3b98"
    assert result["iq_flash_enum"] == iq_enum
    assert result["system_parameter_0x1a"] == parameter
    assert not result["flash_hardware_code_executed"]
    assert not result["hardware_verified"]


@pytest.mark.firmware
@pytest.mark.parametrize("inputs,extra_settings", [({}, {0x159: 3}), ({}, {0x15e: 2}),
                                                  ({"lens_type": 0}, {})])
def test_native_flash_setter_clears_caches_when_existing_guard_rejects(code, inputs, extra_settings):
    probe = FlashProbe(code, mode=0x2006, settings={0x161: 4, **extra_settings}, **inputs)
    result = probe.command(service_mode=6)
    assert result["iq_flash_enum"] == 0
    assert result["system_parameter_0x1a"] == 0


@pytest.mark.firmware
@pytest.mark.parametrize("mode,scene,capture_mode,smart_pro,ae_mode", [
    (0x2002, 9, 0, 0xffffffff, 0x15),
    (0x2003, 0, 2, 0xffffffff, 0),
    (0x2006, 0xc, 1, 0x23, 5),
    (0x200b, 2, 3, 0xffffffff, 1),
    (0x200c, 3, 4, 0xffffffff, 2),
    (0x200d, 4, 5, 0xffffffff, 3),
])
def test_native_ui_scene_and_ae_mode_assignment(code, mode, scene, capture_mode, smart_pro, ae_mode):
    result = AeModeProbe(code, mode=mode).mapping()
    assert result["camera_scene_enum"] == scene
    assert result["parameter_capture_mode"] == capture_mode
    assert result["parameter_smart_pro_mode"] == smart_pro
    assert result["ae_mode"] == ae_mode
    assert result["assignment"]["boundary"] == "native-ae-mode-field-written"
    assert not result["hardware_verified"]
    assert not result["exposure_calculated"]


@pytest.mark.firmware
def test_distinct_3d_mode_input_changes_ae_mapping(code):
    # The firmware's diagnostic explicitly names this input Get3DMode.
    # Do not mistake its separate no-flash program line for ordinary self-shot.
    result = AeModeProbe(code, mode=0x2006).mapping(parameter_3d_mode=1)
    assert result["ae_mode"] == 0x1c


@pytest.fixture(scope="module")
def c03_edits():
    manifest = json.loads(Path("patches/manual-lens-c03.json").read_text())
    return [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
            for o in manifest["operations"]]


@pytest.mark.firmware
@pytest.mark.parametrize("phase", ["initialize", "detach-after-loaded-table"])
def test_c03_default_and_detach_use_one_fixed_aperture_node(code, c03_edits, phase):
    tables = []
    for edits in ((), c03_edits):
        probe = ManualApertureProbe(code, mode=0x2002, detached=True, edits=edits)
        initialized = probe.initialize_default()
        assert initialized["lens_defaults"]["dependencies"]
        if phase == "detach-after-loaded-table":
            probe.load_transport_table([56, 64, 72, 80])
            probe.detach_callback()
        table = probe.aperture_table()
        assert table["lens_fields"] == {"IrisDrive": 0, "LensReady": 0, "OpenAv": 64, "AVZoom": 0}
        assert table["av_set_internal"] == 64
        assert not table["hardware_verified"] and not table["physical_aperture_known"]
        assert probe.call(0x403f0e98, OBJECT)["result"] == 1
        tables.append(table)
    assert tables[0]["table_internal_values"] == list(range(64, 145, 4))
    assert tables[1]["table_internal_values"] == [64]


@pytest.mark.firmware
def test_c03_native_lens_data_loader_replaces_default_with_received_table(code, c03_edits):
    results = []
    for edits in ((), c03_edits):
        probe = ManualApertureProbe(code, edits=edits)
        probe.initialize_default()
        loaded = probe.load_transport_table([56, 64, 72, 80])
        assert loaded["result"] == 1
        probe.write32(probe.PARAM_BUFFER + 0xa4, 56)
        table = probe.aperture_table()
        assert table["lens_fields"]["LensReady"] == 1
        assert table["table_internal_values"] == [56, 64, 72, 80]
        results.append((table, probe.compensate(40, 72)))
    assert results[0] == results[1]


@pytest.mark.firmware
@pytest.mark.parametrize("proposed_av", [64, 68, 80, 96, 128, 144])
def test_c03_native_av_compensation_keeps_fixed_aperture_and_numeric_exposure(code, c03_edits, proposed_av):
    probe = ManualApertureProbe(code, mode=0x2002, detached=True, edits=c03_edits)
    probe.initialize_default()
    probe.aperture_table()
    result = probe.compensate(40, proposed_av)
    assert result["output_internal"] == {"tv": 40 + proposed_av - 64, "av": 64, "sv": 80}
    assert result["tv_plus_av_preserved"]
    assert not result["physical_exposure_verified"]


@pytest.mark.firmware
def test_c04_native_flash_menu_changes_only_selfshot_and_retains_guards(code):
    from tools.verify_candidate import verify_flash_menu
    manifest = json.loads(Path("patches/manual-lens-c04.json").read_text(encoding="utf-8"))
    edits = [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
             for o in manifest["operations"]]
    report = verify_flash_menu(code, edits)
    assert report["modes_compared"] == 65536
    assert report["changed_modes"] == [{"mode": "0x2006", "original": [0], "candidate": [0, 4, 5, 6, 7]}]
    assert len(report["retained_guard_cases"]) == 3
    assert not report["menu_input_to_eeprom_executed"]
    assert not report["physical_flash_verified"]


@pytest.mark.firmware
def test_c04_native_selected_row_writes_settings_and_reopens_without_touching_other_mode(code):
    from tools.verify_candidate import verify_flash_selection
    manifest = json.loads(Path("patches/manual-lens-c04.json").read_text(encoding="utf-8"))
    edits = [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
             for o in manifest["operations"]]
    report = verify_flash_selection(code, edits)
    assert len(report["cases"]) == 15
    assert report["selected_row_to_ram_setting_commit_executed"]
    assert not report["setting_getter_and_setter_modelled"]
    assert not report["settings_nonvolatile_persistence_executed"]
    # Existing independent Program/SelfShot settings survive the same UI model.
    probe = FlashSelectionProbe(code, mode=0x2006, edits=edits, settings={0x15f: 7})
    probe.build_rows(0x2006)
    probe.select_value(4)
    assert probe.build_rows(0x2003)["selected_setting"] == 7
    assert probe.build_rows(0x2006)["selected_setting"] == 4


@pytest.mark.firmware
def test_original_selfshot_cannot_commit_a_setting_absent_from_its_native_rows(code):
    probe = FlashSelectionProbe(code, mode=0x2006)
    assert [r["value"] for r in probe.build_rows(0x2006)["rows"]] == [0]
    with pytest.raises(FirmwareError, match="absent from native menu rows"):
        probe.select_value(4)
    assert not probe.setting_writes


@pytest.fixture(scope="module")
def c05_edits():
    manifest = json.loads(Path("patches/manual-lens-c05.json").read_text(encoding="utf-8"))
    return [(o["offset"], bytes.fromhex(o["before"]), bytes.fromhex(o["after"]))
            for o in manifest["operations"]]


@pytest.mark.firmware
def test_c05_native_selfshot_program_line_and_runtime_flash_enable(code, c05_edits):
    from tools.verify_candidate import verify_native_flash_ae
    report = verify_native_flash_ae(code, c05_edits)
    assert len(report["cases"]) == 15
    assert len(report["strobe_guard_cases"]) == 4
    assert len(report["setting_guard_cases"]) == 3
    assert report["native_program_line_and_output_update_executed"]
    assert not report["metering_sampled"] and not report["flash_hardware_code_executed"]


@pytest.mark.firmware
def test_c05_native_smart_auto_holds_single_aperture_including_fixed_scene(code, c05_edits):
    from tools.verify_candidate import verify_single_aperture_ae
    report = verify_single_aperture_ae(code, c05_edits)
    assert len(report["smart_auto_scene_pairs"]) == 6
    assert len(report["multi_node_mode_pairs"]) == 12
    assert not report["original_9mm_table_captured"]
    assert not report["smart_auto_exposure_verified_on_camera"]


@pytest.mark.firmware
def test_c05_manual_ae_single_aperture_compensates_tv_without_changing_numeric_exposure(code, c05_edits):
    results = []
    for edits in (c05_edits[:-1], c05_edits):
        probe = FlashAeProbe(code, mode=0x200d, detached=True, lens_type=0, edits=edits)
        probe.command()
        prepared = probe.prepare_ae(lens_table=None, av_set=96)
        assert prepared["program_ae_mode"] == 3
        assert prepared["lens"]["table_internal_values"] == [64]
        results.append(probe.evaluate_ae(100)["program_output_tv_av_sv_internal"])
    assert results == [[80, 96, 156], [112, 64, 156]]
    assert sum(results[0][:2]) == sum(results[1][:2])


@pytest.mark.firmware
def test_c05_native_lens_startup_notifications_preserve_states_and_feed_s2(code, c05_edits):
    from tools.verify_candidate import verify_lens_startup_notifications
    report = verify_lens_startup_notifications(code, c05_edits)
    assert len(report["mode_cases"]) == 6
    assert len(report["independent_module_status_cases"]) == 6
    assert report["s2_ready_state_modelled"]
    assert not report["queue_delivery_executed"]
    assert not report["complete_cold_boot_emulated"]
    assert not report["no_contact_short_cold_boot_verified"]


@pytest.mark.firmware
def test_early_no_lens_notification_does_not_set_system_ready_or_emit_capture(code, c05_edits):
    probe = LensStartupProbe(code, mode=0x2002, edits=c05_edits)
    early = probe.callback(5)
    assert not early["after"]["core_0"] & 1
    assert not early["queued_events"]
    deferred = probe.drain_deferred()
    assert not deferred["after"]["core_0"] & 1
    assert [(r["message"], r["event"]) for r in deferred["queued_events"]] == [(0x4d, 5)]
    assert probe.call(0x403f0e98, OBJECT)["result"] == 1
