import faulthandler
import json
from pathlib import Path
import random
import struct
import subprocess
import sys
import zlib

import pytest

from nxmini.app_package import AppPackage, elf_image
from nxmini.baseline import FIRMWARE_SHA256
from nxmini.errors import FirmwareError

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def windows_unicorn_handler():
    enabled = sys.platform == "win32" and faulthandler.is_enabled()
    if enabled:
        faulthandler.disable()
    yield
    if enabled:
        faulthandler.enable()


@pytest.fixture(scope="module")
def app_build(tmp_path_factory):
    pytest.importorskip("unicorn")
    from tools.build_apps import find_compiler
    try:
        find_compiler()
    except SystemExit:
        pytest.skip("Install the local ARM compiler to run compiled application tests")
    path = tmp_path_factory.mktemp("apps")/"build"
    subprocess.run([sys.executable,str(ROOT/"tools/build_apps.py"),"--output",str(path)],
                   cwd=ROOT,check=True,capture_output=True,text=True)
    return path


def minimal_package():
    return AppPackage(struct.pack("<III",0,8,16),8,0,(0,4,8),64,1024,
                      "test",FIRMWARE_SHA256)


def crc_repair(blob):
    data = bytearray(blob)
    data[44:48] = b"\0"*4
    struct.pack_into("<I",data,44,zlib.crc32(data))
    return bytes(data)


def test_package_roundtrip_and_bss_relocations():
    package = minimal_package()
    assert AppPackage.read(package.encode()) == package
    loaded = package.relocate(0x22000000)
    assert struct.unpack_from("<III",loaded) == (0x22000000,0x22000008,0x22000010)
    assert loaded[12:] == b"\0"*8


@pytest.mark.parametrize("base",[0,1,0xfffffff8])
def test_package_rejects_invalid_base(base):
    with pytest.raises(FirmwareError):
        minimal_package().relocate(base)


def test_native_loader_bss_and_relocations_at_three_addresses(app_build):
    from nxmini.app_probe import AppProbe
    data = minimal_package().encode()
    for base in (0x12000000,0x12200000,0x22000000):
        probe = AppProbe((app_build/"loader.elf").read_bytes(),arena=base)
        result,_ = probe.native_validate(data,load=True)
        assert result == 0
        assert probe.uc.mem_read(base,20) == minimal_package().relocate(base)


def test_python_and_native_loader_reject_same_seeded_corruptions(app_build):
    from nxmini.app_probe import AppProbe, ARENA
    probe = AppProbe((app_build/"loader.elf").read_bytes())
    data = minimal_package().encode()
    rng = random.Random(20261019)
    cases = [data[:n] for n in (0,4,80,127,128,len(data)-1)]
    for i in range(60):
        changed = bytearray(data)
        changed[rng.randrange(len(data))] ^= 1 << rng.randrange(8)
        cases.append(bytes(changed))
    for blob in cases:
        with pytest.raises(FirmwareError):
            AppPackage.read(blob)
        result,info = probe.native_validate(blob,load=True)
        assert result < 0
        assert info == b"\xcc"*24
        assert probe.uc.mem_read(ARENA,192*1024) == b"\xa5"*(192*1024)


@pytest.mark.parametrize("offset,value",[(128+12,1),(128+16,0),(128+20,12),(128,20)])
def test_native_loader_rejects_bad_relocation_even_with_repaired_crc(app_build,offset,value):
    from nxmini.app_probe import AppProbe, ARENA
    data = bytearray(minimal_package().encode())
    struct.pack_into("<I",data,offset,value)
    data = crc_repair(data)
    with pytest.raises(FirmwareError):
        AppPackage.read(data)
    probe = AppProbe((app_build/"loader.elf").read_bytes())
    result,info = probe.native_validate(data,load=True)
    assert result == -15 and info == b"\xcc"*24
    assert probe.uc.mem_read(ARENA,192*1024) == b"\xa5"*(192*1024)


def test_game_completes_all_levels_with_normal_inputs(app_build):
    from tools.verify_apps import verify
    report = verify(app_build)
    assert len(report["game"]["five_levels_completed_by_normal_input"]) == 5
    assert not report["executed_on_camera"]


def test_runtime_stops_before_io_with_unbound_adapter(app_build):
    from nxmini.app_probe import SessionProbe
    p = SessionProbe((app_build/"loader.elf").read_bytes())
    assert p.start((app_build/"gold_miner.nxa").read_bytes(),capabilities=0) == -20
    assert p.callback_log == []


@pytest.mark.parametrize("callback,release",[("read_package",0),("sync_code",0),
                                           ("claim_display",0),("present",1)])
def test_runtime_cleans_up_platform_failures(app_build,callback,release):
    from nxmini.app_probe import SessionProbe
    p = SessionProbe((app_build/"loader.elf").read_bytes())
    p.callback_errors[callback] = -1
    assert p.start((app_build/"gold_miner.nxa").read_bytes()) == -21
    assert p.released == release
    assert p.frame() == -21


def test_runtime_private_stack_frame_and_idempotent_cleanup(app_build):
    from nxmini.app_probe import SessionProbe
    p = SessionProbe.from_build(app_build)
    assert [r["name"] for r in p.callback_log] == ["read_package","sync_code","claim_display","present"]
    assert p.frame(1) == 0 and p.game_state()["mode"] == 1
    p.frame(0)
    assert p.frame(8) == 1 and p.released == 1
    p.frame(event=2)
    assert p.released == 1


def test_game_pause_held_buttons_bomb_timeout_restart_and_exit(app_build):
    from nxmini.app_probe import AppProbe
    p = AppProbe.from_build(app_build)
    p.frame(1); p.frame(0)
    p.frame(4)
    frozen = p.game_state()["time_ms"]
    for _ in range(4): p.frame(4,elapsed_ms=100)
    assert p.game_state()["paused"] == 1 and p.game_state()["time_ms"] == frozen
    p.frame(0); p.frame(4); p.frame(0)
    # The initial ray points at a large nugget. Swing naturally to it, then
    # discard the attached item with a bomb; do not fabricate item state.
    for _ in range(200):
        s = p.game_state()
        fire = int(s["phase"] == 0 and s["angle"] == 18)
        p.frame(fire,elapsed_ms=40)
        if p.game_state()["attached"] >= 0: break
    assert p.game_state()["attached"] >= 0
    p.frame(2)
    assert p.game_state()["bombs"] == 1 and p.game_state()["attached"] == -1
    # Let actual game time expire, then retry from the loss screen.
    for _ in range(610):
        p.frame(0,elapsed_ms=100)
        if p.game_state()["mode"] == 3: break
    assert p.game_state()["mode"] == 3
    p.frame(1)
    s = p.game_state()
    assert s["mode"] == 1 and s["level"] == 1 and s["score"] == 0 and s["time_ms"] == 60000
    assert p.frame(8) == 1


def test_game_rejects_invalid_abi_context_and_framebuffer(app_build):
    from nxmini.app_probe import AppProbe, CONTEXT, HOST, FRAME, STATE
    p = AppProbe.from_build(app_build)
    for field,value in ((0,2),(1,4),(2,0),(3,480),(5,10),(9,0),(10,8),(12,99)):
        args = [1,HOST.size,FRAME+32,320,240,320,0,0,33,STATE,1024,1,1]
        args[field] = value
        p.uc.mem_write(CONTEXT,HOST.pack(*args))
        assert p.call(p.entry,CONTEXT) == -1


def test_elf_rejects_hard_float_and_unsupported_absolute_relocation(app_build):
    original = (app_build/"gold_miner.elf").read_bytes()
    elf_image(original)
    blob = bytearray(original)
    struct.pack_into("<I",blob,36,0x5000400)
    with pytest.raises(FirmwareError): elf_image(bytes(blob))
    blob = bytearray(original)
    shoff = struct.unpack_from("<I",blob,32)[0]
    shnum = struct.unpack_from("<H",blob,48)[0]
    for i in range(shnum):
        section = struct.unpack_from("<10I",blob,shoff+i*40)
        if section[1] == 9 and section[5]:
            blob[section[4]+4] = 43  # R_ARM_MOVW_ABS_NC, deliberately unsupported
            break
    with pytest.raises(FirmwareError): elf_image(bytes(blob))


def test_sdk_compiles_and_runs_a_separate_user_app_with_bss(app_build,tmp_path):
    from nxmini.app_probe import SessionProbe,FRAME
    source = tmp_path/"my_tool.c"
    source.write_text('''#include "nxe_app.h"
static unsigned counter;
int nxe_app_entry(NxeHost *h) {
    if (h->event == NXE_STOP) return NXE_EXIT;
    if (h->event == NXE_INIT) counter = 0;
    h->pixels[0] = (uint16_t)++counter;
    return h->pressed & NXE_BACK ? NXE_EXIT : NXE_CONTINUE;
}
''',encoding="ascii")
    build = tmp_path/"custom-build"
    subprocess.run([sys.executable,str(ROOT/"tools/build_apps.py"),"--source",str(source),
                    "--app-id","my_tool","--state-bytes","64","--stack-bytes","1024",
                    "--output",str(build)],check=True,capture_output=True,text=True,cwd=ROOT)
    data = (build/"my_tool.nxa").read_bytes()
    package = AppPackage.read(data)
    assert package.bss_bytes >= 4 and package.app_id == "my_tool"
    assert str(source).replace('\\','/') in json.loads((build/"build.json").read_text())["sources"]
    p = SessionProbe((build/"loader.elf").read_bytes())
    assert p.start(data) == 0
    assert p.uc.mem_read(FRAME+32,2) == b"\x01\x00"
    assert p.frame() == 0
    assert p.uc.mem_read(FRAME+32,2) == b"\x02\x00"
    assert p.frame(8) == 1 and p.released == 1


def test_sdk_software_division_matches_integer_arithmetic(app_build):
    from nxmini.app_probe import AppProbe,LOADER
    from unicorn.arm_const import UC_ARM_REG_R1
    p = AppProbe((app_build/"loader.elf").read_bytes())
    for n,d in ((0,1),(1,1),(7,3),(0xffffffff,1),(0xffffffff,0x80000000),(0x80000000,17)):
        q = p.call(LOADER+p.symbols["__aeabi_uidivmod"],n,d)
        assert (q & 0xffffffff,p.uc.reg_read(UC_ARM_REG_R1)) == divmod(n,d)
    for n,d in ((-7,3),(7,-3),(-7,-3),(-2147483648,7),(2147483647,-3)):
        q = p.call(LOADER+p.symbols["__aeabi_idivmod"],n & 0xffffffff,d & 0xffffffff)
        expected = (abs(n)//abs(d)) * (-1 if (n<0)!=(d<0) else 1)
        remainder = p.uc.reg_read(UC_ARM_REG_R1)
        assert q == expected and remainder == (n-expected*d)&0xffffffff
@pytest.mark.firmware
def test_original_file_wrapper_returns_byte_count_and_masks_close_status():
    pytest.importorskip("unicorn")
    from nxmini.app_platform_probe import NativeFileProbe
    path = ROOT/"work/nxmini-1.10-verified/decoded/chunk-01.bin"
    if not path.exists(): pytest.skip("Extract original 1.10 firmware for wrapper probes")
    rows = NativeFileProbe(path.read_bytes()).evidence()["records"]
    assert [r["returned_byte_count"] for r in rows if r["operation"] == "read-wrapper"] == [8,3,0]
    assert [r["result"] for r in rows if r["operation"] == "close-wrapper"] == [1,1]


@pytest.mark.firmware
def test_original_canvas_uses_32_bit_stores_and_preserves_bounds():
    pytest.importorskip("unicorn")
    from nxmini.app_platform_probe import NativeCanvasProbe
    path = ROOT/"work/nxmini-1.10-verified/decoded/chunk-01.bin"
    if not path.exists(): pytest.skip("Extract original 1.10 firmware for canvas probes")
    result = NativeCanvasProbe(path.read_bytes()).evidence()
    assert result["cpu_pixel_store_bits"] == 32
    assert result["rectangle_geometry_and_guards_verified"]
    assert not result["real_display_presented"]
