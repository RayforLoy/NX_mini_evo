"""Build the same freestanding ARM game used by the C loader and PC harness."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nxmini.app_package import AppPackage, elf_image
from nxmini.baseline import FIRMWARE_SHA256


def find_compiler():
    requested = os.environ.get("NXE_ARM_GCC")
    candidates = ([Path(requested)] if requested else [])
    candidates += sorted((ROOT/"tools/local").glob("arm-gnu-toolchain-*/bin/arm-none-eabi-gcc.exe"))
    if shutil.which("arm-none-eabi-gcc"):
        candidates.append(Path(shutil.which("arm-none-eabi-gcc")))
    for p in candidates:
        if p.is_file():
            return p
    raise SystemExit("Run tools/bootstrap_app_toolchain.py or set NXE_ARM_GCC")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"work/apps-build-A01")
    parser.add_argument("--rebuild",action="store_true",help="Replace this builder's previous outputs")
    parser.add_argument("--source",type=Path,action="append",help="Custom C source; repeat for multiple sources")
    parser.add_argument("--app-id",default="gold_miner")
    parser.add_argument("--state-bytes",type=int,default=1024)
    parser.add_argument("--stack-bytes",type=int,default=8192)
    args = parser.parse_args()
    if (not re.fullmatch(r"[a-z0-9_-]{1,31}",args.app_id) or args.app_id == "loader"
            or not 0 < args.state_bytes <= 8192 or not 1024 <= args.stack_bytes <= 32768
            or args.stack_bytes % 8):
        raise SystemExit("Invalid app ID, state size or stack size")
    app_sources = [p.resolve() for p in args.source] if args.source else [ROOT/"firmware/apps/gold_miner/gold_miner.c"]
    if not all(p.is_file() and p.suffix.lower() in (".c",".s") for p in app_sources):
        raise SystemExit("Application sources must be existing C or assembly files")
    output = args.output.resolve()
    if output.exists() and (not args.rebuild or not (output/".nxe-build").is_file()):
        raise SystemExit("Output exists; choose a new directory or --rebuild for a prior build")
    output.mkdir(parents=True,exist_ok=True)
    (output/".nxe-build").write_text("NXE application builder\n",encoding="ascii")
    compiler = find_compiler()
    version = subprocess.check_output([str(compiler),"--version"],text=True).splitlines()[0]
    app = ROOT/"firmware/apps"
    common = [str(compiler),"-std=c11","-Os","-march=armv5te","-marm","-mfloat-abi=soft",
              "-mno-unaligned-access","-ffreestanding","-fno-builtin","-fno-pic","-fno-pie",
              "-fno-unwind-tables","-fno-asynchronous-unwind-tables","-ffunction-sections",
              "-fdata-sections","-fstack-usage","-Wa,--noexecstack","-Wall","-Wextra","-Werror",
              "-I",str(app/"include"),"-I",str(app/"runtime")]
    builds = {
        args.app_id: (app_sources+[app/"runtime/freestanding.c"],"nxe_app_entry"),
        "loader": ([app/"runtime/nxe_loader.c",app/"runtime/nxe_runtime.c",
                    app/"runtime/nxe_call_on_stack.S",app/"runtime/freestanding.c"],"nxe_validate"),
    }
    records = {}
    for name,(sources,entry) in builds.items():
        objects = []
        for index,source in enumerate(sources):
            obj = output/f"{name}-{index}-{source.stem}.o"
            subprocess.run(common+["-c",str(source),"-o",str(obj)],check=True)
            objects.append(obj)
        elf = output/f"{name}.elf"
        subprocess.run([str(compiler),"-nostdlib","-marm","-march=armv5te",
                        f"-Wl,-T,{app/'module.ld'}",f"-Wl,-e,{entry}","-Wl,--emit-relocs",
                        "-Wl,--build-id=none",f"-Wl,-Map,{output/(name+'.map')}",
                        *map(str,objects),"-o",str(elf)],check=True)
        image,bss,start,relocs,symbols = elf_image(elf.read_bytes())
        (output/f"{name}.bin").write_bytes(image)
        (output/f"{name}.symbols.json").write_text(json.dumps(symbols,indent=2)+"\n",encoding="utf8")
        record = {"image_bytes":len(image),"bss_bytes":bss,"entry_offset":start,
                  "relocations":list(relocs),"sha256":hashlib.sha256(image).hexdigest()}
        if name == args.app_id:
            package = AppPackage(image,bss,start,relocs,args.state_bytes,args.stack_bytes,args.app_id,FIRMWARE_SHA256).encode()
            (output/f"{name}.nxa").write_bytes(package)
            record.update(package_bytes=len(package),package_sha256=hashlib.sha256(package).hexdigest(),
                          state_bytes=args.state_bytes,stack_bytes=args.stack_bytes,framebuffer_bytes=320*240*2)
        records[name] = record
    source_files = sorted(set((ROOT/"firmware/apps").rglob("*"))|set(app_sources))
    report = {"schema_version":1,"compiler":version,"target":"armv5te-arm-soft-float-le",
              "firmware_adapter_profile_sha256":FIRMWARE_SHA256,"modules":records,
              "sources":{(str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in source_files if p.is_file()},
              "camera_adapter_bound":False,"executed_on_camera":False,"flash_ready":False}
    (output/"build.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"output":str(output),"compiler":version,"modules":records},indent=2))


if __name__ == "__main__":
    main()
