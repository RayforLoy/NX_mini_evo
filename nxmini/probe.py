"""Bounded ARM function probes, with explicit modelled dependencies.

This executes selected original instructions in Unicorn, never camera hardware.
It does not emulate REALOS, the ISP, flash charging or a camera boot.
"""
import struct

from . import baseline
from .errors import FirmwareError
from .util import align, sha256

BASE = 0x40000000
CORE = 0x419f2424
OBJECT = 0x419e0000
STACK = 0x50000000
VTABLE = 0x40773cdc


def eeprom_catalog(decoded: bytes):
    """1.10's section-4 table is {LE32 id, LE32 name_address}."""
    result = []
    for offset in range(0xb38fc, 0xb3f74 - 7, 8):
        number, pointer = struct.unpack_from("<II", decoded, offset)
        position = pointer - 0x40d45000
        if not 0 <= position < len(decoded) or not number < 0x1000:
            raise FirmwareError("EEPROM description table differs from 1.10")
        end = decoded.find(b"\0", position)
        if end < position or end - position > 120:
            raise FirmwareError("Invalid EEPROM name")
        name = decoded[position:end].decode("ascii")
        if not name.startswith("EEP_"):
            raise FirmwareError("Unexpected EEPROM description")
        result.append({"id": number, "name": name.strip(), "table_offset": offset,
                       "string_offset": position, "section": 4,
                       "evidence": "static-id-and-name-pointer-pair", "hardware_verified": False})
    return result


class ArmProbe:
    """Each instance starts with zeroed model RAM and an original section 1."""

    def __init__(self, decoded: bytes, *, mode=0x2003, detached=False, adapter=False,
                 lens_type=1, nolens_flag=False, settings=None, edits=()):
        if sha256(decoded) != baseline.DECODED_SHA256[1]:
            raise FirmwareError("ARM probe requires original official 1.10 section 1")
        from unicorn import Uc, UC_ARCH_ARM, UC_MODE_ARM, UC_HOOK_CODE
        from unicorn.arm_const import UC_CPU_ARM_CORTEX_A9
        self.uc = Uc(UC_ARCH_ARM, UC_MODE_ARM)
        self.uc.ctl_set_cpu_model(UC_CPU_ARM_CORTEX_A9)
        self.uc.mem_map(BASE, align(len(decoded), 4096))
        self.uc.mem_write(BASE, decoded)
        self.uc.mem_map(0x419d0000, 0x40000)
        self.uc.mem_map(STACK, 0x20000)
        self.write32(0x4099019c, OBJECT)
        self.write32(OBJECT, VTABLE)
        self.write32(CORE + 0x28, 0x10 if detached else 0)
        # The mode occupies the upper half of the +0x28 flags word.
        self.uc.mem_write(CORE + 0x2a, struct.pack("<HH", mode, mode))
        self.write32(CORE + 0xc, 0x40000000 if nolens_flag else 0)
        self.uc.mem_write(CORE + 0xe4, bytes([int(adapter)]))
        self.settings = dict(settings or {})
        self.lens_type = lens_type
        self.dependencies = []
        self.allowed = [(0x403eea24, 0x403ef878), (0x40346e48, 0x40348f14),
                        (0x403f0e84, 0x403f117c), (0x404489f8, 0x40448b80),
                        (0x406d4324, 0x406d435c)]
        for offset, before, after in edits:
            if len(before) != len(after) or decoded[offset:offset + len(before)] != before:
                raise FirmwareError("Probe edit preimage mismatch")
            self.uc.mem_write(BASE + offset, after)
        self.uc.hook_add(UC_HOOK_CODE, self._code)

    def write32(self, address, value):
        self.uc.mem_write(address, struct.pack("<I", value))

    def _return(self, value):
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_PC, UC_ARM_REG_LR
        self.uc.reg_write(UC_ARM_REG_R0, value)
        self.uc.reg_write(UC_ARM_REG_PC, self.uc.reg_read(UC_ARM_REG_LR))

    def _code(self, uc, address, size, _):
        from unicorn.arm_const import UC_ARM_REG_R0
        if address in getattr(self, "stop_at", {}):
            uc.emu_stop()
        elif address == STACK:
            uc.emu_stop()
        elif address == 0x40356da8:
            key = uc.reg_read(UC_ARM_REG_R0)
            value = self.settings.get(key, 0)
            self.dependencies.append({"kind": "modelled-setting-read", "id": key, "value": value})
            self._return(value)
        elif address == 0x40448440:
            self.dependencies.append({"kind": "modelled-lens-type", "value": self.lens_type})
            self._return(self.lens_type)
        elif address == 0x404414ac:
            self.dependencies.append({"kind": "modelled-camera-state-query", "value": 0})
            self._return(0)
        elif not any(start <= address < end for start, end in self.allowed):
            raise FirmwareError(f"Unmodelled ARM dependency at {address:#x}")

    def call(self, entry, *arguments, stop_at=None, timeout_us=1_000_000):
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2,
                                       UC_ARM_REG_R3, UC_ARM_REG_SP, UC_ARM_REG_LR,
                                       UC_ARM_REG_PC, UC_ARM_REG_CPSR)
        self.dependencies.clear()
        self.stop_at = dict(stop_at or {})
        self.uc.reg_write(UC_ARM_REG_CPSR, 0x13)
        values = tuple(arguments) + (0, 0, 0, 0)
        for register, value in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), values):
            self.uc.reg_write(register, value)
        self.uc.reg_write(UC_ARM_REG_SP, STACK + 0x1f000)
        self.uc.reg_write(UC_ARM_REG_LR, STACK)
        self.uc.emu_start(entry, STACK, timeout=timeout_us, count=10000)
        position = self.uc.reg_read(UC_ARM_REG_PC)
        if position in self.stop_at:
            return {"result": None, "stopped_at": hex(position), "boundary": self.stop_at[position],
                    "dependencies": list(self.dependencies), "hardware_verified": False,
                    "whole_camera_emulated": False}
        if position != STACK:
            raise FirmwareError("ARM probe exceeded its execution budget")
        return {"result": self.uc.reg_read(UC_ARM_REG_R0), "dependencies": list(self.dependencies),
                "hardware_verified": False, "whole_camera_emulated": False}


class CaptureProbe(ArmProbe):
    """Runs the original S2 routing code up to named capture/interception boundaries.

    The original mode readiness callback runs too. Camera status, logs, mode
    cancellation and a UI cleanup helper are explicit models. The verified mode
    startS2Mode is the default stopping boundary. through_start executes it and
    the native timer selection, then stops at original mode +0xf0 photo dispatch.
    No sensor/REALOS/flash call runs.
    """

    MODE_OBJECT = OBJECT + 0x2000
    CAMERA_OBJECT = OBJECT + 0x5000
    CAMERA_VTABLE = OBJECT + 0x6000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        # The app state getter reads a separate shared RAM page, not CORE.
        self.uc.mem_map(0x414a4000, 0x1000)
        mode = kwargs.get("mode", 0x2003)
        table = {0x2002: 0x4077452c, 0x2003: 0x40770f10,
                 0x2006: 0x407728d4}.get(mode, 0x4076abd4)
        self.write32(self.MODE_OBJECT, table)
        self.photo_dispatch = int.from_bytes(decoded[table - BASE + 0xf0:table - BASE + 0xf4], "little")
        self.write32(0x4098f1cc, self.CAMERA_OBJECT)
        self.write32(self.CAMERA_OBJECT, self.CAMERA_VTABLE)
        self.write32(self.CAMERA_VTABLE + 0x34, STACK + 0x2034)
        self.write32(self.CAMERA_OBJECT + 0x4f8, self.CAMERA_VTABLE)
        self.write32(self.CAMERA_VTABLE + 0xa4, STACK + 0x20a4)
        self.write32(0x4099131c, OBJECT + 0x3000)
        self.write32(OBJECT + 0x3000, 0x40780074)
        self.allowed += [(0x4014daf0, 0x4014deb8), (0x406c7b00, 0x406c8028),
                         (0x406c7180, 0x406c718c), (0x406ca734, 0x406ca790),
                         (0x4027bde0, 0x4027c0d8), (0x4014eb60, 0x4014ee68),
                         (0x4023cb88, 0x4023cec0), (0x4044102c, 0x40441120)]

    def _code(self, uc, address, size, data):
        if address in getattr(self, "stop_at", {}):
            return super()._code(uc, address, size, data)
        if address in (STACK + 0x2034, 0x403e9168, 0x4027bc20):
            label = {STACK + 0x2034: "modelled-camera-slot-0x34",
                     0x403e9168: "modelled-timer-state",
                     0x4027bc20: "modelled-mode-cancel"}[address]
            self.dependencies.append({"kind": label, "value": 0})
            self._return(0)
        elif address in (0x403e76f0, 0x403e6f80, 0x4023a670):
            self.dependencies.append({"kind": "modelled-log-or-ui-cleanup",
                                      "address": hex(address), "value": 0})
            self._return(0)
        elif address in (0x403df948, 0x40444934, 0x4027c154, STACK + 0x20a4):
            from unicorn.arm_const import UC_ARM_REG_R1
            self.dependencies.append({"kind": "modelled-clock-or-ui-or-timer-setter",
                                      "address": hex(address),
                                      "argument1": uc.reg_read(UC_ARM_REG_R1), "value": 0})
            self._return(0)
        else:
            super()._code(uc, address, size, data)

    def s2_route(self, *, state4=0x80000, state8=0xc, app_state=0, through_start=False):
        # A settled live-view state with the same flags in original/modified runs.
        self.write32(CORE + 4, state4)
        self.write32(CORE + 8, state8)
        self.write32(CORE + 0x330, 1)
        self.uc.mem_write(0x414a4ec0, bytes([app_state]))
        endpoints = {0x406c7b00: "lens-event-interception",
                                  0x4044463c: "half-shutter-cancel-api",
                                  0x4015bfd8: "lens-electronics-query",
                                  0x4014ee48: "mode-change-to-s1",
                                  0x4014eb60: "mode-start-s2"}
        if through_start:
            del endpoints[0x4014eb60]
            endpoints[self.photo_dispatch] = "mode-photo-dispatch"
        return self.call(0x4014daf0, self.MODE_OBJECT, 0, stop_at=endpoints)

    def lens_event(self, event):
        if event not in (2, 6):
            raise FirmwareError("Only the verified detached event paths 2 and 6 are modelled")
        return self.call(0x406c7b00, OBJECT, event,
                         stop_at={0x406c7f34: "detached-event-2-popup-path",
                                  0x406c8008: "detached-event-6-popup-path"})


class LensStartupProbe(CaptureProbe):
    """Native lens callback/cache/deferred messages, without booting the camera.

    Queue delivery, logs and the transition to a settled shooting state remain
    explicit models. Event 5 supplies the native detached state instead of
    pre-seeding that flag. This does not emulate detection electronics, the
    sensor-ready interrupt, REALOS scheduling or a complete cold boot.
    """

    ENGINE_OBJECT = OBJECT + 0x7000
    ENGINE_VTABLE = OBJECT + 0x8000
    MODULE_OBJECT = OBJECT + 0xa000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        self.write32(0x4098f240, self.ENGINE_OBJECT)
        self.write32(self.ENGINE_OBJECT, self.ENGINE_VTABLE)
        self.write32(self.ENGINE_VTABLE + 4, STACK + 0x2104)
        self.queue_events = []
        self.allowed += [(0x40267f60, 0x402681c0), (0x403efadc, 0x403f05dc),
                         (0x403f06ec, 0x403f1040), (0x402f7664, 0x402f7a40)]

    def _code(self, uc, address, size, data):
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3
        if address == STACK + 0x2104:
            self.queue_events.append({"boundary": "modelled-engine-message-queue",
                                      "message": uc.reg_read(UC_ARM_REG_R1),
                                      "event": uc.reg_read(UC_ARM_REG_R2),
                                      "payload": uc.reg_read(UC_ARM_REG_R3)})
            self._return(0)
        elif address == 0x403ee4a4:
            self.queue_events.append({"boundary": "modelled-deferred-event-queue-api",
                                      "message": 0x4d, "event": uc.reg_read(UC_ARM_REG_R0),
                                      "payload": uc.reg_read(UC_ARM_REG_R1)})
            self._return(0)
        elif address == 0x403e7894:
            self.dependencies.append({"kind": "modelled-event-log"})
            self._return(0)
        else:
            super()._code(uc, address, size, data)

    def _startup_state(self):
        return {"core_0": int.from_bytes(self.uc.mem_read(CORE, 4), "little"),
                "core_24": int.from_bytes(self.uc.mem_read(CORE + 0x24, 4), "little"),
                "core_28": int.from_bytes(self.uc.mem_read(CORE + 0x28, 4), "little"),
                "app_38": int.from_bytes(self.uc.mem_read(OBJECT + 0x38, 4), "little"),
                "core_e4": int.from_bytes(self.uc.mem_read(CORE + 0xe4, 1), "little")}

    def callback(self, event, *, ready_bit0=False, payload=0):
        if event not in (5, 6, 7) or (event == 7 and not ready_bit0):
            raise FirmwareError("Only detach/attach and post-boot ready notifications are modelled")
        previous = int.from_bytes(self.uc.mem_read(CORE, 4), "little")
        self.write32(CORE, previous & ~1 | int(ready_bit0))
        self.queue_events.clear()
        before = self._startup_state()
        execution = self.call(0x40267f60, 0, event, payload)
        return {"event_input": event, "core_ready_bit0_modelled": ready_bit0,
                "before": before, "after": self._startup_state(),
                "queued_events": list(self.queue_events), "execution": execution,
                "complete_cold_boot_emulated": False, "hardware_verified": False}

    def drain_deferred(self):
        self.queue_events.clear()
        before = self._startup_state()
        execution = self.call(0x403f06ec, OBJECT, 0, 0, 0,
                              stop_at={0x40448968: "original-tail-ui-status-update"})
        return {"before": before, "after": self._startup_state(),
                "queued_events": list(self.queue_events), "execution": execution,
                "queue_delivery_executed": False, "complete_cold_boot_emulated": False}

    def assume_ready_and_s2(self):
        previous = int.from_bytes(self.uc.mem_read(CORE, 4), "little")
        self.write32(CORE, previous | 1)
        return {"ready_bit0_set_by_model": True,
                "settled_state4_state8_app_state_and_core330_modelled": True,
                "route": self.s2_route(through_start=True), "hardware_verified": False}

    def module_status(self, value):
        self.write32(self.MODULE_OBJECT + 0x1f8, value & 0xffffffff)
        return {"raw_status_modelled": value, "execution": self.call(0x402f7664, self.MODULE_OBJECT),
                "status_to_callback_conversion_executed": False, "hardware_verified": False}


class FlashProbe(ArmProbe):
    """Execute the effective getter and real CCamSystemImpl parameter setter.

    The original derived IQ vtable, adjustment thunk and both native RAM writes
    run. Only object allocation, EEPROM, lens classification and logs are models.
    Execution stops before returning from the setter. Neither a parameter cache
    write nor this bounded execution establishes physical flash operation.
    """

    MENU_OBJECT = OBJECT + 0x3000
    CAMERA_OBJECT = OBJECT + 0x5000
    CAMERA_VTABLE = 0x4076f5f0
    IQ_VTABLE = 0x4076fb88
    IQ_STATE = 0x419f0538
    PARAM_OBJECT = OBJECT + 0x7000
    PARAM_BUFFER = OBJECT + 0x8000
    STOP_AFTER_WRITE = 0x406c3b98

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        self.write32(0x4099131c, self.MENU_OBJECT)
        self.write32(self.MENU_OBJECT, 0x40780074)
        self.write32(0x4098f1cc, self.CAMERA_OBJECT)
        self.write32(self.CAMERA_OBJECT, self.CAMERA_VTABLE)
        self.write32(self.CAMERA_OBJECT + 0x4f8, self.IQ_VTABLE)
        # Same field layout as constructors 0x40267810 and 0x40344be0;
        # allocation is modelled, not the implementations of either RAM setter.
        self.write32(self.CAMERA_OBJECT + 0x524, self.PARAM_OBJECT)
        self.write32(self.PARAM_OBJECT, 0x40772044)
        self.write32(self.PARAM_OBJECT + 8, self.PARAM_BUFFER)
        self.allowed += [(0x40442af4, 0x40442cac), (0x40695360, 0x4069536c),
                         (0x406c3ad0, 0x406c3ba0), (0x4040afd8, 0x4040afe8),
                         (0x40345b48, 0x40345b60)]

    def _code(self, uc, address, size, data):
        if address == 0x403e6f80:
            self.dependencies.append({"kind": "modelled-log", "address": hex(address)})
            self._return(0)
        else:
            super()._code(uc, address, size, data)

    def command(self, *, service_mode=0):
        self.uc.mem_write(CORE + 0x74, bytes([service_mode]))
        # Sentinels distinguish a native write from untouched zero-initialized RAM.
        self.uc.mem_write(self.IQ_STATE + 0x11, b"\xfe")
        self.write32(self.PARAM_BUFFER + 0x1a * 4, 0xffffffff)
        result = self.call(0x40442af4, OBJECT,
                           stop_at={self.STOP_AFTER_WRITE: "flash-parameter-caches-written"})
        if result.get("boundary") != "flash-parameter-caches-written":
            raise FirmwareError("Original flash wrapper did not reach the native cache-write boundary")
        result["iq_flash_enum"] = int.from_bytes(self.uc.mem_read(self.IQ_STATE + 0x11, 1), "little")
        result["system_parameter_0x1a"] = int.from_bytes(
            self.uc.mem_read(self.PARAM_BUFFER + 0x1a * 4, 4), "little")
        result["flash_hardware_code_executed"] = False
        return result


class FlashMenuProbe(FlashProbe):
    """Native CMenuFlash option dispatch, optionally including its row guards.

    Requested-only mode records calls to the row helper. The guarded path runs
    that original helper, CMenuLib availability and metadata lookup; only its
    final UI row sink, memcpy, EEPROM, lens classification and logs are models.
    No drawing, input events, setting persistence or physical flash runs.
    """

    WIDGET = OBJECT + 0xa000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        # CMenuLib availability reads shared UI counts before selector dispatch.
        self.uc.mem_map(0x41896000, 0x1000)
        self.allowed += [(0x403ff11c, 0x403ff318), (0x403492b0, 0x40349800),
                         (0x40349924, 0x4034a4d4), (0x40346d44, 0x40346d78)]
        self.rows = []
        self.collect_requested = False

    def _code(self, uc, address, size, data):
        from unicorn.arm_const import (UC_ARM_REG_R0, UC_ARM_REG_R1,
                                       UC_ARM_REG_R2, UC_ARM_REG_SP)
        if address == 0x403ff11c and self.collect_requested:
            self.rows.append(uc.reg_read(UC_ARM_REG_R1))
            self._return(0)
        elif address == 0x403e6c64:
            target, source, length = (uc.reg_read(r) for r in
                                      (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            if not STACK <= target <= target + length <= STACK + 0x20000 or length != 0x40:
                raise FirmwareError("Unexpected flash menu metadata copy")
            uc.mem_write(target, bytes(uc.mem_read(source, length)))
            self.dependencies.append({"kind": "modelled-memcpy", "length": length})
            self._return(target)
        elif address == 0x403fe49c:
            selectable = int.from_bytes(uc.mem_read(uc.reg_read(UC_ARM_REG_SP) + 12, 4), "little")
            self.rows.append({"value": uc.reg_read(UC_ARM_REG_R2),
                              "native_row_flag": selectable})
            self.dependencies.append({"kind": "modelled-ui-row-sink", **self.rows[-1]})
            self._return(0)
        else:
            super()._code(uc, address, size, data)

    def options(self, mode, *, guarded=True):
        if not isinstance(mode, int) or not 0 <= mode <= 0xffff:
            raise FirmwareError("UI mode must be a 16-bit integer")
        self.uc.mem_write(CORE + 0x2a, struct.pack("<H", mode))
        self.rows = []
        self.collect_requested = not guarded
        self.write32(self.WIDGET + 0xcc, self.settings.get(0x161 if mode == 0x2006 else 0x15f, 0))
        # This small dispatcher retains the 10,000-instruction bound. Avoid a
        # watchdog thread per call during the exhaustive 16-bit mode comparison.
        result = self.call(0x403ff24c, self.WIDGET, timeout_us=0)
        return {"mode": hex(mode), "rows": list(self.rows), "guarded": guarded,
                "execution": result, "ui_rendered": False, "setting_written": False,
                "flash_hardware_code_executed": False}


class FlashSelectionProbe(FlashMenuProbe):
    """Original row storage and selected-row commit through RAM settings writes.

    UI object allocation, inactive popup state, mutexes, timing, memcpy, lens
    classification and logs remain models. Settings reads/writes execute native
    code against a model-initialized RAM table. This is not nonvolatile storage
    emulation, a touch/key event driver, or physical flash verification.
    """

    UI_ROOT = OBJECT + 0xb000
    SETTINGS_BUFFER = OBJECT + 0xc000
    SETTINGS_DESCRIPTOR = 0x4098f22c
    SETTINGS_COUNT = 0x400

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        from unicorn import UC_HOOK_MEM_WRITE
        self.uc.mem_map(0x414a4000, 0x1000)
        self.write32(0x40999300, self.UI_ROOT)
        self.write32(self.UI_ROOT + 0xab8, 0xffffffff)  # No active popup.
        self.write32(self.WIDGET, 0x4076a87c)
        for offset, value in ((0, self.SETTINGS_COUNT), (4, self.SETTINGS_BUFFER), (8, 1), (12, 0)):
            self.write32(self.SETTINGS_DESCRIPTOR + offset, value)
        for key, value in self.settings.items():
            if not 0 <= key < self.SETTINGS_COUNT or not 0 <= value <= 255:
                raise FirmwareError("Model initial setting is outside the byte table")
            self.uc.mem_write(self.SETTINGS_BUFFER + key, bytes([value]))
        self.allowed += [(0x403fe49c, 0x403fe51c), (0x403ff318, 0x403ff3bc),
                         (0x403ff434, 0x403ff4c4), (0x40346b88, 0x40346c04),
                         (0x40346dd0, 0x40346e48), (0x40345eb0, 0x4034697c),
                         (0x40356da8, 0x40356de8), (0x403570b0, 0x40357144),
                         (0x4031a98c, 0x4031aa0c)]
        self.setting_writes = []
        self.uc.hook_add(UC_HOOK_MEM_WRITE, self._setting_write,
                         begin=self.SETTINGS_BUFFER, end=self.SETTINGS_BUFFER + self.SETTINGS_COUNT - 1)

    def _setting_write(self, uc, access, address, size, value, data):
        self.setting_writes.append({"id": address - self.SETTINGS_BUFFER,
                                    "bytes": size, "value": value})

    def _code(self, uc, address, size, data):
        from unicorn.arm_const import UC_ARM_REG_R0
        if address in (0x403fe49c, 0x40356da8):
            # Bypass the parent model: allow the original row sink / byte getter.
            if address == 0x40356da8:
                self.dependencies.append({"kind": "native-setting-read-entry",
                                          "id": uc.reg_read(UC_ARM_REG_R0)})
            return
        if address == 0x403e6dfc:
            if not uc.reg_read(UC_ARM_REG_R0):
                raise FirmwareError("Native settings table assertion failed")
            self.dependencies.append({"kind": "modelled-successful-assert"})
            self._return(0)
        elif address in (0x402452e8, 0x4024530c, 0x40516258, 0x403df948):
            self.dependencies.append({"kind": "modelled-mutex-or-timing", "address": hex(address)})
            self._return(0)
        else:
            super()._code(uc, address, size, data)

    def build_rows(self, mode):
        if not isinstance(mode, int) or not 0 <= mode <= 0xffff:
            raise FirmwareError("UI mode must be a 16-bit integer")
        self.uc.mem_write(CORE + 0x2a, struct.pack("<H", mode))
        result = self.call(0x403ff318, self.WIDGET, 1,
                           stop_at={0x403ff3bc: "native-flash-widget-rows-stored"})
        if result.get("boundary") != "native-flash-widget-rows-stored":
            raise FirmwareError("Original flash widget did not build its rows")
        count = int.from_bytes(self.uc.mem_read(self.WIDGET + 0xc4, 4), "little")
        if not 0 <= count <= 20:
            raise FirmwareError("Native flash menu row count exceeded capacity")
        self.stored_rows = [{"value": int.from_bytes(self.uc.mem_read(self.WIDGET + 0xe8 + i * 0x20, 4), "little"),
                             "native_row_flag": int.from_bytes(self.uc.mem_read(self.WIDGET + 0xec + i * 0x20, 4), "little")}
                            for i in range(count)]
        return {"mode": hex(mode), "rows": list(self.stored_rows),
                "selected_setting": int.from_bytes(self.uc.mem_read(self.WIDGET + 0xcc, 4), "little"),
                "selected_row": int.from_bytes(self.uc.mem_read(self.WIDGET + 0x1c, 4), "little"),
                "execution": result, "ui_rendered": False, "hardware_verified": False}

    def select_value(self, value, *, service_mode=6):
        values = [row["value"] for row in getattr(self, "stored_rows", [])]
        if value not in values:
            raise FirmwareError("Requested flash setting is absent from native menu rows")
        index = values.index(value)
        self.uc.mem_write(self.WIDGET + 0x5a, struct.pack("<H", index))
        self.uc.mem_write(CORE + 0x74, bytes([service_mode]))
        before = bytes(self.uc.mem_read(self.SETTINGS_BUFFER, self.SETTINGS_COUNT))
        dirty_before = int.from_bytes(self.uc.mem_read(self.SETTINGS_DESCRIPTOR + 12, 4), "little")
        self.setting_writes.clear()
        self.uc.mem_write(self.IQ_STATE + 0x11, b"\xfe")
        self.write32(self.PARAM_BUFFER + 0x1a * 4, 0xffffffff)
        execution = self.call(0x403ff4b4, self.WIDGET,
                              stop_at={self.STOP_AFTER_WRITE: "selected-row-to-native-flash-caches"})
        if execution.get("boundary") != "selected-row-to-native-flash-caches":
            raise FirmwareError("Original row commit did not reach flash parameter caches")
        after = bytes(self.uc.mem_read(self.SETTINGS_BUFFER, self.SETTINGS_COUNT))
        return {"selected_value": value, "selected_row_index_modelled_as_input": index,
                "ram_setting_changes": [{"id": i, "before": a, "after": b}
                                        for i, (a, b) in enumerate(zip(before, after)) if a != b],
                "native_setting_writes": list(self.setting_writes),
                "ram_table_dirty_before": dirty_before,
                "ram_table_dirty": int.from_bytes(self.uc.mem_read(self.SETTINGS_DESCRIPTOR + 12, 4), "little"),
                "iq_flash_enum": int.from_bytes(self.uc.mem_read(self.IQ_STATE + 0x11, 1), "little"),
                "system_parameter_0x1a": int.from_bytes(self.uc.mem_read(self.PARAM_BUFFER + 0x1a * 4, 4), "little"),
                "execution": execution, "settings_nonvolatile_persistence_executed": False,
                "key_or_touch_event_dispatch_executed": False,
                "flash_hardware_code_executed": False, "hardware_verified": False}


class FlashEntryProbe(FlashSelectionProbe):
    """Run the outer MENU item iterator, including native flash availability.

    The iterator and flash guard run original ARM; other items' availability
    and the final item sink are explicit models. No GUI is rendered. This
    starts before CMenuFlash, unlike the earlier option/selection probes.
    """

    OUTER_MENU = OBJECT + 0xd000
    OUTER_VTABLE = 0x4076cc98

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        self.write32(self.OUTER_MENU, self.OUTER_VTABLE)
        self.allowed += [(0x40535d34, 0x40535ed0), (0x40245240, 0x40245294),
                         (0x4031a408, 0x4031a48c), (0x403f117c, 0x403f11ac)]
        self.outer_rows = []

    def _code(self, uc, address, size, data):
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
        if address == 0x40345ba8:
            self.outer_rows.append({"selector": uc.reg_read(UC_ARM_REG_R1),
                                    "disabled": bool(uc.reg_read(UC_ARM_REG_R2))})
            self.dependencies.append({"kind": "modelled-outer-menu-item-sink",
                                      **self.outer_rows[-1]})
            self._return(0)
        elif address == 0x403492b0 and uc.reg_read(UC_ARM_REG_R1) != 10:
            self.dependencies.append({"kind": "modelled-unrelated-item-availability",
                                      "selector": uc.reg_read(UC_ARM_REG_R1), "value": 1})
            self._return(1)
        elif address == 0x403e06c8:
            target, length = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1))
            if length != 0x44 or not self.UI_ROOT + 0x18 <= target < self.UI_ROOT + 0xab8:
                raise FirmwareError("Unexpected outer menu row initialization")
            uc.mem_write(target, bytes(length))
            self._return(target)
        else:
            super()._code(uc, address, size, data)

    def outer_options(self, mode):
        if not isinstance(mode, int) or not 0 <= mode <= 0xffff:
            raise FirmwareError("UI mode must be a 16-bit integer")
        self.uc.mem_write(CORE + 0x2a, struct.pack("<H", mode))
        self.outer_rows = []
        entry = int.from_bytes(self.uc.mem_read(self.OUTER_VTABLE + 0x8c, 4), "little")
        if entry != 0x40535d34:
            raise FirmwareError("Outer CMenuProgram item iterator binding differs")
        execution = self.call(entry, self.OUTER_MENU)
        return {"mode": hex(mode), "rows": list(self.outer_rows), "execution": execution,
                "native_flash_availability_executed": any(r["selector"] == 10 for r in self.outer_rows),
                "unrelated_item_availability_modelled": True,
                "outer_item_sink_modelled": True, "ui_rendered": False,
                "hardware_verified": False}


class SmartPanelFlashProbe(FlashEntryProbe):
    """Native Fn mode dispatch, grid storage and flash item enumeration.

    Item allocation and non-flash item callbacks are models. Flash list storage,
    availability and enabled flags execute native code. Flash rectangle setup
    and drawing are models; this is not a rendered screen or physical input.
    """

    REGISTRY = 0x40991324
    ITEM_BASE = 0x419d0000
    ITEM_VTABLE = 0x419d4000
    FLASH_ITEM = ITEM_BASE + 0x800
    PANEL = OBJECT + 0xe000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        for index in range(1, 19):
            item = self.ITEM_BASE + index * 0x100
            self.write32(self.REGISTRY + index * 4, item)
            self.write32(item, 0x40773b90 if item == self.FLASH_ITEM else self.ITEM_VTABLE)
            self.write32(item + 0x14, 2)  # Model unrelated items as selectable.
        for slot in (4, 8, 0x10):
            self.write32(self.ITEM_VTABLE + slot, STACK + 0x3000 + slot)
        self.write32(self.PANEL, 0x40772418)
        self.allowed += [(0x40509bd0, 0x40509d00), (0x40508ec8, 0x40509410),
                         (0x40509460, 0x40509bd0), (0x40286990, 0x40286a2c),
                         (0x406f917c, 0x406f9540), (0x4031950c, 0x40319554),
                         (0x40516210, 0x40516258), (0x40288c74, 0x40288da4),
                         (0x403196f4, 0x40319740)]
        self.fn_requested_values = []
        self.collect_fn_values = False

    def _code(self, uc, address, size, data):
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R2
        if address == 0x40319310:
            item = uc.reg_read(UC_ARM_REG_R0)
            if item != self.FLASH_ITEM:
                raise FirmwareError("Unexpected flash rectangle initialization object")
            self.write32(item + 0x70, 0)
            self.dependencies.append({"kind": "modelled-flash-rectangle-setup-and-list-reset"})
            self._return(1)
        elif address == 0x4031950c and self.collect_fn_values:
            self.fn_requested_values.append(uc.reg_read(UC_ARM_REG_R2))
            self._return(0)
        elif address in (STACK + 0x3004, STACK + 0x3008, STACK + 0x3010,
                         0x4028709c, 0x406f92c0, 0x40319740, 0x403e6f80):
            self.dependencies.append({"kind": "modelled-fn-drawing-title-or-unrelated-callback",
                                      "address": hex(address)})
            self._return(1)
        else:
            super()._code(uc, address, size, data)

    def fn_values(self, mode, *, guarded=True):
        if not isinstance(mode, int) or not 0 <= mode <= 0xffff:
            raise FirmwareError("UI mode must be a 16-bit integer")
        self.uc.mem_write(CORE + 0x2a, struct.pack("<H", mode))
        self.fn_requested_values = []
        self.collect_fn_values = not guarded
        execution = self.call(0x406f917c, self.FLASH_ITEM, timeout_us=0)
        count = int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x70, 4), "little")
        if count > 16:
            raise FirmwareError("Fn flash value list exceeded its native capacity")
        values = ([int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x30 + i * 4, 4), "little")
                   for i in range(count)] if guarded else list(self.fn_requested_values))
        return {"mode": hex(mode), "values": values, "guarded": guarded, "execution": execution,
                "ui_rendered": False, "hardware_verified": False}

    def fn_layout(self, mode):
        if not isinstance(mode, int) or not 0 <= mode <= 0xffff:
            raise FirmwareError("UI mode must be a 16-bit integer")
        self.uc.mem_write(CORE + 0x2a, struct.pack("<H", mode))
        self.collect_fn_values = False
        self.uc.mem_write(self.PANEL + 0x34, bytes(5 * 4 * 4))
        execution = self.call(0x40509bd0, self.PANEL)
        top, bottom, limit = struct.unpack("<HHH", self.uc.mem_read(self.PANEL + 0x24, 6))
        grid = []
        for row in range(5):
            cells = []
            for column in range(4):
                pointer = int.from_bytes(self.uc.mem_read(self.PANEL + 0x34 + row * 16 + column * 4, 4), "little")
                if pointer and (pointer < self.ITEM_BASE + 0x100 or pointer >= self.ITEM_BASE + 0x1300 or
                                (pointer - self.ITEM_BASE) % 0x100):
                    raise FirmwareError("Native Fn grid points outside the model item registry")
                cells.append((pointer - self.ITEM_BASE) // 0x100 if pointer else None)
            grid.append(cells)
        count = int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x70, 4), "little")
        values = [int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x30 + i * 4, 4), "little")
                  for i in range(count)]
        cells = int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x2a, 2), "little")
        enabled = bool(int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x14, 4), "little") & 2)
        return {"mode": hex(mode), "grid_registry_indices": grid, "pane_fields": [top, bottom, limit],
                "flash_grid_cell_count": cells, "flash_present": bool(cells),
                "flash_values": values, "flash_selectable": bool(cells) and enabled,
                "execution": execution, "ui_rendered": False, "hardware_verified": False}

    def fn_next_value(self):
        """Run the original next-value callback through settings/cache writes."""
        count = int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x70, 4), "little")
        enabled = int.from_bytes(self.uc.mem_read(self.FLASH_ITEM + 0x14, 4), "little") & 2
        if not 0 < count <= 16 or not enabled:
            raise FirmwareError("Fn flash item has no available selectable values")
        before = bytes(self.uc.mem_read(self.SETTINGS_BUFFER, self.SETTINGS_COUNT))
        self.setting_writes.clear()
        self.uc.mem_write(CORE + 0x74, b"\x06")
        self.uc.mem_write(self.IQ_STATE + 0x11, b"\xfe")
        self.write32(self.PARAM_BUFFER + 0x1a * 4, 0xffffffff)
        execution = self.call(0x406f9508, self.FLASH_ITEM,
                              stop_at={self.STOP_AFTER_WRITE: "fn-next-value-to-native-flash-caches"})
        if execution.get("boundary") != "fn-next-value-to-native-flash-caches":
            raise FirmwareError("Native Fn next-value callback did not reach flash caches")
        after = bytes(self.uc.mem_read(self.SETTINGS_BUFFER, self.SETTINGS_COUNT))
        return {"selected_setting": after[0x161],
                "ram_setting_changes": [{"id": i, "before": a, "after": b}
                                        for i, (a, b) in enumerate(zip(before, after)) if a != b],
                "native_setting_writes": list(self.setting_writes),
                "iq_flash_enum": int.from_bytes(self.uc.mem_read(self.IQ_STATE + 0x11, 1), "little"),
                "system_parameter_0x1a": int.from_bytes(self.uc.mem_read(self.PARAM_BUFFER + 0x68, 4), "little"),
                "execution": execution, "physical_key_or_touch_event_executed": False,
                "settings_nonvolatile_persistence_executed": False,
                "hardware_verified": False}


class AeModeProbe(FlashProbe):
    """Original UI-to-camera scene mapping and CAeInfoIF AEMode assignment.

    Object allocation and other input fields are models. No metering, program
    line evaluation or flash operation runs. In particular this does not prove
    Smart Auto exposure without electronic aperture information.
    """

    INFO_OBJECT = OBJECT + 0x9000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        self.mode = kwargs.get("mode", 0x2003)
        self.write32(self.INFO_OBJECT, 0x4078040c)  # Original CAeInfoIF vtable.
        self.write32(self.INFO_OBJECT + 4, self.PARAM_OBJECT)
        self.allowed += [(0x404410e4, 0x40441384), (0x40695040, 0x4069504c),
                         (0x406c2bdc, 0x406c2f18), (0x4031390c, 0x40313dc8),
                         (0x40344c8c, 0x40344c98), (0x4034537c, 0x40345388),
                         (0x40345ac4, 0x40345ae8), (0x40316ea8, 0x40316eb4)]

    def mapping(self, *, parameter_3d_mode=0):
        from unicorn.arm_const import UC_ARM_REG_R4
        # Original diagnostic 0x403174f0 names parameter +0x144 Get3DMode.
        # Its source and on-device value remain external inputs to this probe.
        self.write32(self.PARAM_BUFFER + 0x144, parameter_3d_mode)
        self.write32(self.PARAM_BUFFER + 0x47 * 4, 0xffffffff)
        self.write32(self.PARAM_BUFFER + 0x72 * 4, 0xffffffff)
        configured = self.call(0x404410e4, OBJECT, self.mode)
        # Execute the original call and STRH sequence, with the same this-object
        # in R4 as the enclosing 0x40316d98 function, then stop before its next call.
        self.uc.reg_write(UC_ARM_REG_R4, self.INFO_OBJECT)
        assigned = self.call(0x40316ea8, stop_at={0x40316eb4: "native-ae-mode-field-written"})
        return {
            "mode": hex(self.mode), "get_3d_mode_input": parameter_3d_mode,
            "camera_scene_enum": int.from_bytes(self.uc.mem_read(self.IQ_STATE, 1), "little"),
            "parameter_capture_mode": int.from_bytes(
                self.uc.mem_read(self.PARAM_BUFFER + 0x47 * 4, 4), "little"),
            "parameter_smart_pro_mode": int.from_bytes(
                self.uc.mem_read(self.PARAM_BUFFER + 0x72 * 4, 4), "little"),
            "ae_mode": int.from_bytes(self.uc.mem_read(self.INFO_OBJECT + 0x80, 2), "little"),
            "setter_dependencies": configured["dependencies"],
            "assignment": assigned,
            "exposure_calculated": False, "hardware_verified": False,
        }


class ManualApertureProbe(AeModeProbe):
    """Default/detach CAeLensIF table, native AE table copy and AV compensation.

    The two libc memory primitives and allocation are explicit models. Lens
    defaults, getters, table construction and compensation execute original ARM.
    This covers a numeric AE constraint, never sensor metering or exposure.
    """

    PROGRAM_OBJECT = OBJECT + 0xa000
    LENS_OBJECT = OBJECT + 0xc000
    EXPOSURE_OBJECT = OBJECT + 0xe000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        lens_global = struct.unpack_from("<I", decoded, 0x521218)[0]
        self.write32(lens_global, self.LENS_OBJECT)
        self.allowed += [(0x40520c50, 0x40520d98), (0x40520d98, 0x40520dc0),
                         (0x40520dc0, 0x40520ffc), (0x40521f8c, 0x40522004),
                         (0x4052219c, 0x40522238), (0x40316db0, 0x40316e18),
                         (0x405229ec, 0x405229f4),
                         (0x403138c0, 0x4031390c), (0x40345184, 0x403451c0),
                         (0x4043ef20, 0x4043efac), (0x403162f8, 0x40316354),
                         (0x40249ed0, 0x40249f34), (0x4043ff74, 0x4043fff8),
                         (0x40521998, 0x40521adc), (0x40521d94, 0x40521db0)]

    def _code(self, uc, address, size, data):
        if address == 0x40342ca8 and getattr(self, "transport_table", None) is not None:
            from unicorn.arm_const import UC_ARM_REG_R0
            target = uc.reg_read(UC_ARM_REG_R0)
            if target != self.LENS_OBJECT + 0xc8:
                raise FirmwareError("Unexpected native lens-table transport buffer")
            uc.mem_write(target, bytes(self.transport_table))
            self.dependencies.append({"kind": "modelled-lens-table-transport",
                                      "values": list(self.transport_table)})
            self._return(len(self.transport_table))
        elif address in (0x403e06c8, 0x403e0f6c):
            from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
            target = uc.reg_read(UC_ARM_REG_R0)
            value = 0 if address == 0x403e06c8 else uc.reg_read(UC_ARM_REG_R1) & 0xff
            length = uc.reg_read(UC_ARM_REG_R1 if address == 0x403e06c8 else UC_ARM_REG_R2)
            if not 0 <= length <= 0x2000 or not 0x419d0000 <= target <= target + length <= 0x41a10000:
                raise FirmwareError("Memory primitive exceeded the probe's model RAM")
            uc.mem_write(target, bytes([value]) * length)
            self.dependencies.append({"kind": "modelled-libc-memory-fill", "address": hex(address),
                                      "target": hex(target), "length": length, "byte": value})
            self._return(target)
        else:
            super()._code(uc, address, size, data)

    def initialize_default(self):
        constructor = self.call(0x40520c50, self.LENS_OBJECT)
        defaults = self.call(0x40520fd4, self.LENS_OBJECT)
        program = self.call(0x4043ef20, self.PROGRAM_OBJECT)
        return {"lens_constructor": constructor, "lens_defaults": defaults,
                "program_constructor": program}

    def aperture_table(self):
        from unicorn.arm_const import UC_ARM_REG_R4, UC_ARM_REG_R5
        self.uc.reg_write(UC_ARM_REG_R4, self.INFO_OBJECT)
        self.uc.reg_write(UC_ARM_REG_R5, self.LENS_OBJECT)
        assigned = self.call(0x40316db0, self.LENS_OBJECT,
                             stop_at={0x40316e18: "native-lens-ae-fields-written"})
        av_set = self.call(0x403138c0, self.INFO_OBJECT, 0)
        # Run the original table-get/copy fragment with the same parent registers.
        self.uc.reg_write(UC_ARM_REG_R4, self.PROGRAM_OBJECT + 4)
        self.uc.reg_write(UC_ARM_REG_R5, self.INFO_OBJECT)
        copied = self.call(0x403162f8, stop_at={0x40316354: "native-program-aperture-table-written"})
        count = int.from_bytes(self.uc.mem_read(self.PROGRAM_OBJECT + 0x6e, 2), "little", signed=True)
        if not 0 <= count <= 64:
            raise FirmwareError("Native aperture table count is outside its capacity")
        nodes = list(struct.unpack("<" + "h" * count,
                                   self.uc.mem_read(self.PROGRAM_OBJECT + 0x70, count * 2)))
        fields = {name: int.from_bytes(self.uc.mem_read(self.INFO_OBJECT + offset, 2), "little", signed=True)
                  for name, offset in (("IrisDrive", 0xb8), ("LensReady", 0xb0),
                                       ("OpenAv", 0xb6), ("AVZoom", 0xae))}
        return {"lens_fields": fields, "av_set_internal": av_set["result"],
                "table_count": count, "table_internal_values": nodes,
                "lens_assignment": assigned, "av_set_query": av_set, "table_copy": copied,
                "hardware_verified": False, "physical_aperture_known": False}

    def load_transport_table(self, nodes):
        if not 1 <= len(nodes) <= 64 or any(not 0 <= node <= 255 for node in nodes) or list(nodes) != sorted(set(nodes)):
            raise FirmwareError("Synthetic lens table must contain 1..64 increasing byte values")
        self.transport_table = list(nodes)
        try:
            return self.call(0x40521998, self.LENS_OBJECT)
        finally:
            self.transport_table = None

    def detach_callback(self):
        return self.call(0x40521d94, self.LENS_OBJECT)

    def compensate(self, tv, av, sv=80):
        self.uc.mem_write(self.EXPOSURE_OBJECT, struct.pack("<hhh", tv, av, sv) + bytes(18))
        execution = self.call(0x40249ed0, self.PROGRAM_OBJECT, self.EXPOSURE_OBJECT)
        result_tv, result_av, result_sv = struct.unpack("<hhh", self.uc.mem_read(self.EXPOSURE_OBJECT, 6))
        return {"input_internal": {"tv": tv, "av": av, "sv": sv},
                "output_internal": {"tv": result_tv, "av": result_av, "sv": result_sv},
                "tv_plus_av_preserved": tv + av == result_tv + result_av,
                "execution": execution, "physical_exposure_verified": False}


class FlashAeProbe(FlashSelectionProbe):
    """Selected flash option through native AE parameter construction/program line.

    Runs on synthetic RAM with explicit scene, strobe and fixed lens inputs.
    Metering samples, calibration, peripheral I/O and nonvolatile settings are
    not emulated. Numeric AE output and iFlashEnable are software observations,
    never proof of physical flash charging, triggering or exposure correctness.
    """

    INFO_OBJECT = OBJECT + 0xd000
    PROGRAM_OBJECT = OBJECT + 0xe000
    LENS_OBJECT = 0x41a10000
    SCENE_OBJECT = 0x41a11000
    OUTPUT_OBJECT = 0x41a12000

    def __init__(self, decoded, **kwargs):
        super().__init__(decoded, **kwargs)
        self.mode = kwargs.get("mode", 0x2006)
        self.uc.mem_map(0x41a10000, 0x10000)
        self.write32(self.INFO_OBJECT, 0x4078040c)
        self.write32(self.INFO_OBJECT + 4, self.PARAM_OBJECT)
        for global_address, target in ((0x40994a70, self.INFO_OBJECT),
                                        (0x40994ad0, self.PROGRAM_OBJECT),
                                        (0x40994a8c, self.LENS_OBJECT),
                                        (0x409952e0, self.SCENE_OBJECT),
                                        (0x40994a58, self.OUTPUT_OBJECT)):
            self.write32(global_address, target)
        self.write32(0x40994a74, 0)  # Optional calibration-table refresh not requested.
        self.allowed += [(0x404410e4, 0x40441384), (0x40695040, 0x4069504c),
                         (0x406c2bdc, 0x406c2f18), (0x4031390c, 0x40313dc8),
                         (0x40344c8c, 0x40344c98), (0x4034537c, 0x40345388),
                         (0x40345ac4, 0x40345ae8), (0x40316ea8, 0x40316eb4),
                         (0x40316cb4, 0x40316d08), (0x40344fc8, 0x40344fdc),
                         (0x40316f18, 0x40316f24), (0x40315da4, 0x40316388),
                         (0x40520c50, 0x40520ffc), (0x40521f8c, 0x40522004),
                         (0x4052219c, 0x40522238), (0x405229ec, 0x405229f4),
                         (0x40316db0, 0x40316e18), (0x403138c0, 0x4031390c),
                         (0x40345184, 0x403451c0), (0x40521998, 0x40521adc),
                         (0x4043ef20, 0x4043efac), (0x4043efb4, 0x4043efe8),
                         (0x402683e4, 0x40268440), (0x402684c8, 0x402684d0),
                         (0x4043ff74, 0x4043fff8), (0x4043f3b4, 0x4043f9a4),
                         (0x40249b00, 0x4024ad50), (0x4024b38c, 0x4024b6cc),
                         (0x4053d2d0, 0x4053d680),
                         (0x403dfbc0, 0x403dfdec), (0x40313428, 0x40313450),
                         (0x4053b014, 0x4053b03c), (0x4053b060, 0x4053b0dc),
                         (0x4053b21c, 0x4053b428), (0x4053d1e4, 0x4053d220)]

    @staticmethod
    def _model_memory_span(address, length):
        return any(start <= address <= address + length <= end for start, end in
                   ((0x419d0000, 0x41a20000), (STACK, STACK + 0x20000)))

    def _code(self, uc, address, size, data):
        from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2
        if address == 0x40342ca8 and getattr(self, "transport_table", None) is not None:
            target = uc.reg_read(UC_ARM_REG_R0)
            if target != self.LENS_OBJECT + 0xc8:
                raise FirmwareError("Unexpected lens table destination")
            uc.mem_write(target, bytes(self.transport_table))
            self.dependencies.append({"kind": "modelled-lens-table-transport", "values": self.transport_table})
            self._return(len(self.transport_table))
        elif address in (0x403e06c8, 0x403e0f6c):
            target = uc.reg_read(UC_ARM_REG_R0)
            value = 0 if address == 0x403e06c8 else uc.reg_read(UC_ARM_REG_R1) & 255
            length = uc.reg_read(UC_ARM_REG_R1 if address == 0x403e06c8 else UC_ARM_REG_R2)
            if not 0 <= length <= 0x2000 or not self._model_memory_span(target, length):
                raise FirmwareError("AE memory fill exceeded model RAM")
            uc.mem_write(target, bytes([value]) * length)
            self.dependencies.append({"kind": "modelled-libc-memory-fill", "length": length, "byte": value})
            self._return(target)
        elif address in (0x403e6c64, 0x403e5b34):
            target, source, length = (uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2))
            if not 0 <= length <= 0x2000 or not self._model_memory_span(target, length):
                raise FirmwareError("AE memory copy exceeded model RAM")
            uc.mem_write(target, bytes(uc.mem_read(source, length)))
            self.dependencies.append({"kind": "modelled-libc-memory-copy", "address": hex(address), "length": length})
            self._return(target)
        else:
            super()._code(uc, address, size, data)

    def prepare_ae(self, *, strobe_in_ext=0, external_genuine=0, external_ready=0, charging=0,
                   scene_value=80, lens_table=(64,), smart_auto_scene=0, av_set=64):
        from unicorn.arm_const import UC_ARM_REG_R4
        mapping = AeModeProbe.mapping(self)
        initialized = ManualApertureProbe.initialize_default(self)
        transport = (ManualApertureProbe.load_transport_table(self, list(lens_table))
                     if lens_table is not None else None)
        scene = self.call(0x402683e4, self.SCENE_OBJECT)
        self.write32(self.SCENE_OBJECT + 0x284, scene_value)
        # Named by the original CAeInfoIF diagnostic. These are controlled inputs,
        # not measured light, original 9mm lens data, or camera calibration.
        fields = {0x84: 0, 0x86: 160, 0x88: 80, 0x8a: av_set, 0x8c: 0,
                  0x96: 0, 0x98: 0, 0xaa: smart_auto_scene, 0xbc: strobe_in_ext, 0xc4: external_genuine,
                  0xc2: external_ready, 0xd0: charging, 0xce: 80}
        for offset, value in fields.items():
            self.uc.mem_write(self.INFO_OBJECT + offset, struct.pack("<h", value))
        lens = ManualApertureProbe.aperture_table(self)
        self.uc.reg_write(UC_ARM_REG_R4, self.INFO_OBJECT)
        flash = self.call(0x40316f18, stop_at={0x40316f24: "native-ae-flash-mode-assigned"})
        built = self.call(0x403160c8, self.INFO_OBJECT)
        return {"mapping": mapping, "initialized": initialized, "synthetic_transport": transport,
                "scene_constructor": scene, "scene_value_modelled": scene_value,
                "modelled_info_fields": {hex(k): v for k, v in fields.items()}, "lens": lens,
                "flash_mode_assignment": flash, "program_builder": built,
                "info_flash_mode": int.from_bytes(self.uc.mem_read(self.INFO_OBJECT + 0x92, 2), "little"),
                "program_ae_mode": int.from_bytes(self.uc.mem_read(self.PROGRAM_OBJECT + 0x36, 2), "little"),
                "program_flash_mode": int.from_bytes(self.uc.mem_read(self.PROGRAM_OBJECT + 0x3a, 2), "little"),
                "program_flash_available": int.from_bytes(self.uc.mem_read(self.PROGRAM_OBJECT + 0x66, 2), "little"),
                "physical_flash_verified": False}

    def evaluate_ae(self, brightness):
        if not isinstance(brightness, int) or not -32768 <= brightness <= 32767:
            raise FirmwareError("AE brightness input must be a signed halfword")
        self.uc.mem_write(self.OUTPUT_OBJECT + 0x28, b"\xff\xff")
        result = self.call(0x4053b364, self.OUTPUT_OBJECT, brightness)
        cache = bytes(self.uc.mem_read(self.INFO_OBJECT + 0x548 + 6 * 0x16, 0x16))
        return {"brightness_modelled_internal": brightness, "execution": result,
                "program_output_tv_av_sv_internal": list(struct.unpack_from("<hhh", cache)),
                "program_output_flags": {"offset_6": cache[6], "offset_7": cache[7]},
                "program_output_flash_enable": cache[9],
                "runtime_iFlashEnable": int.from_bytes(self.uc.mem_read(self.OUTPUT_OBJECT + 0x28, 2), "little"),
                "metering_sampled": False, "hardware_verified": False,
                "flash_hardware_code_executed": False}
