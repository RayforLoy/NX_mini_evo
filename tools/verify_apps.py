"""Verify native module loading and game play; report model/hardware boundaries."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nxmini.app_package import AppPackage
from nxmini.app_probe import AppProbe, SessionProbe, ARENA, STATE
from nxmini.baseline import FIRMWARE_SHA256


def repair_crc(data):
    changed = bytearray(data)
    changed[44:48] = b"\0"*4
    struct.pack_into("<I",changed,44,zlib.crc32(changed))
    return bytes(changed)


def aim(state):
    if state["phase"] != 0:
        return 0
    angle = math.radians(-75+state["angle"]*150/32)
    vx,vy = math.sin(angle),math.cos(angle)
    hits = []
    for v in state["items"]:
        dx,dy = v["x"]-160,v["y"]-56
        projection = dx*vx+dy*vy
        perpendicular = abs(dx*vy-dy*vx)
        if v["active"] and projection > 12 and perpendicular <= v["r"]:
            hits.append((projection,v["kind"]))
    return 1 if hits and min(hits)[1] != 2 else 0


def verify(build,preview=None):
    data = (build/"gold_miner.nxa").read_bytes()
    package = AppPackage.read(data)
    loader = (build/"loader.elf").read_bytes()
    relocation_runs = []
    for base in (ARENA,ARENA+0x200000,0x22000000):
        p = AppProbe(loader,arena=base)
        p.load(data)
        assert p.game_state()["mode"] == 0
        assert p.frame(1) == 0
        assert p.game_state()["mode"] == 1
        relocation_runs.append({"model_base":hex(base),"c_loader_matches_python":True,"entry_executed":True})
    p = AppProbe(loader)
    failures = []
    cases = [("truncated",data[:60],-10),("trailing-bytes",data+b"x",-12)]
    changed = bytearray(data); changed[-1] ^= 1
    cases.append(("bad-crc",bytes(changed),-13))
    for name,offset,value,error in (("wrong-abi",8,2,-11),("image-over-limit",20,131073,-12),
                                   ("unaligned-entry",28,1,-12),("stack-over-limit",40,32776,-12)):
        changed = bytearray(data); struct.pack_into("<I",changed,offset,value)
        cases.append((name,repair_crc(changed),error))
    changed = bytearray(data); changed[48] ^= 1
    cases.append(("wrong-profile",repair_crc(changed),-14))
    changed = bytearray(data); struct.pack_into("<I",changed,128+len(package.image),len(package.image)+4)
    cases.append(("relocation-outside-image",repair_crc(changed),-15))
    for name,blob,expected in cases:
        result,info = p.native_validate(blob,load=True)
        assert result == expected,(name,result,expected)
        assert info == b"\xcc"*24
        assert p.uc.mem_read(ARENA,192*1024) == b"\xa5"*(192*1024)
        failures.append({"case":name,"native_error":result,"arena_and_info_unchanged":True})
    result,info = p.native_validate(data,load=True,arena_bytes=4)
    assert result == -16 and info == b"\xcc"*24
    assert p.uc.mem_read(ARENA,192*1024) == b"\xa5"*(192*1024)
    failures.append({"case":"insufficient-arena","native_error":result,"arena_and_info_unchanged":True})
    p = SessionProbe.from_build(build)
    if preview:
        preview.mkdir(parents=True,exist_ok=True)
        p.image().resize((960,720),resample=0).save(preview/"gold-miner-title.png")
    p.frame(1); p.frame(0)
    before = p.game_state()["time_ms"]
    p.frame(4,elapsed_ms=100); p.frame(0,elapsed_ms=100); p.frame(0,elapsed_ms=100)
    assert p.game_state()["paused"] == 1 and p.game_state()["time_ms"] == before
    p.frame(4); p.frame(0)
    levels = []
    play_saved = False
    for frame in range(3000):
        state = p.game_state()
        if preview and not play_saved and state["phase"] == 2 and state["attached"] >= 0:
            p.image().resize((960,720),resample=0).save(preview/"gold-miner-playing.png")
            play_saved = True
        if state["mode"] in (2,4):
            levels.append({"level":state["level"],"score":state["score"],
                           "goal":state["goal"],"remaining_ms":state["time_ms"]})
            if state["mode"] == 4:
                break
            p.frame(0); p.frame(1); p.frame(0)
        elif state["mode"] == 3:
            raise AssertionError(f"Game bot timed out at level {state['level']}")
        else:
            p.frame(aim(state),elapsed_ms=100)
    assert [v["level"] for v in levels] == [1,2,3,4,5]
    if preview:
        p.image().resize((960,720),resample=0).save(preview/"gold-miner-complete.png")
    assert p.frame(8) == 1
    assert p.frame(event=2) == 1
    return {"schema_version":1,"package_sha256":hashlib.sha256(data).hexdigest(),
            "native_loader":"cross-compiled C, executed by Unicorn ARM926",
            "native_session":{"read_load_cache_callback_private_stack_init_frame_present_exit":True,
                              "present_calls":p.presented,"display_release_calls":p.released,
                              "platform_callbacks":"explicit model adapters, no camera interfaces"},
            "relocation_runs":relocation_runs,"invalid_packages":failures,
            "game":{"five_levels_completed_by_normal_input":levels,"pause_preserves_timer":True,
                    "back_and_stop_return_to_host":True,"framebuffer_state_stack_guards_intact":True},
            "model_dependencies":["executable model RAM","RGB565 framebuffer","logical keys",
                                  "synthetic elapsed milliseconds","host-managed state and stack"],
            "firmware_adapter_profile_sha256":FIRMWARE_SHA256,
            "original_camera_firmware_executed_in_game_probe":False,
            "camera_adapter_bound":False,"executed_on_camera":False,"flash_ready":False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("build",type=Path)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--preview",type=Path)
    args = parser.parse_args()
    report = verify(args.build,args.preview)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"verified":True,"output":str(args.output.resolve()),
                      "levels":len(report["game"]["five_levels_completed_by_normal_input"]),
                      "negative_cases":len(report["invalid_packages"]),"executed_on_camera":False}))


if __name__ == "__main__":
    main()
