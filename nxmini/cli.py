import argparse
import json
from pathlib import Path
import sys
import urllib.request
import zipfile

from . import baseline, relc
from .errors import FirmwareError
from .firmware import Firmware, rebuild
from .util import new_output, sha256, write_json


def integer(value):
    return int(value, 0)


def parser():
    root = argparse.ArgumentParser(description="NX mini offline firmware and transfer research tools")
    sub = root.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="Download the hash-pinned official firmware")
    fetch.add_argument("--directory", type=Path, default=Path("data/firmware"))
    for command in ("inspect", "verify", "extract", "analyze"):
        p = sub.add_parser(command)
        p.add_argument("firmware", type=Path)
        if command in ("extract", "analyze"):
            p.add_argument("output", type=Path)
        else:
            p.add_argument("--json", dest="json_output", type=Path)
            p.add_argument("--deep", action="store_true")
    repack = sub.add_parser("repack", help="Rebuild an extraction; output is not flash-approved")
    repack.add_argument("directory", type=Path)
    repack.add_argument("output", type=Path)
    for name in ("decode", "encode"):
        p = sub.add_parser(name)
        p.add_argument("input", type=Path)
        p.add_argument("output", type=Path)
    patch = sub.add_parser("patch", help="Apply a hash-bound offline patch")
    patch.add_argument("firmware", type=Path)
    patch.add_argument("manifest", type=Path)
    patch.add_argument("output", type=Path)
    patch.add_argument("--reverse", action="store_true")
    disasm = sub.add_parser("disasm")
    disasm.add_argument("input", type=Path)
    disasm.add_argument("--offset", type=integer, default=0)
    disasm.add_argument("--size", type=integer, default=256)
    disasm.add_argument("--base", type=integer, default=0)
    disasm.add_argument("--mode", choices=["arm", "thumb"], required=True)
    disasm.add_argument("--output", type=Path)
    serve = sub.add_parser("serve", help="Run the experimental PC AutoBackup receiver")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--advertise-ip", required=True)
    serve.add_argument("--port", type=int, default=52235)
    serve.add_argument("--output", type=Path, required=True)
    serve.add_argument("--state", type=Path, required=True)
    serve.add_argument("--name", default="[PC]NX mini evo")
    serve.add_argument("--ssdp", action="store_true")
    serve.add_argument("--camera-ip", action="append", default=[], help="Only accept these camera IPs; repeatable")
    style = sub.add_parser("style", help="Render an offline colour reference; not a camera filter")
    style.add_argument("input", type=Path)
    style.add_argument("profile", type=Path)
    style.add_argument("output", type=Path)
    cube = sub.add_parser("style-export")
    cube.add_argument("profile", type=Path)
    cube.add_argument("output", type=Path)
    cube.add_argument("--size", type=int, default=17)
    pairing = sub.add_parser("pairing-template", help="Create a local INI template; does not write to a camera")
    pairing.add_argument("--state", type=Path, required=True)
    pairing.add_argument("--mac", required=True)
    pairing.add_argument("--name", default="[PC]NX mini evo")
    pairing.add_argument("output", type=Path)
    trace = sub.add_parser("trace", help="Inventory passive digital capture timing; no protocol assumptions")
    trace.add_argument("input", type=Path)
    trace.add_argument("--time-column", default="Time [s]")
    trace.add_argument("--json", dest="json_output", type=Path)
    discovery = sub.add_parser("discover")
    discovery.add_argument("--interface", required=True)
    discovery.add_argument("--timeout", type=float, default=5)
    discovery.add_argument("--json", dest="json_output", type=Path)
    description = sub.add_parser("describe")
    description.add_argument("url")
    description.add_argument("--json", dest="json_output", type=Path)
    return root


def fetch_firmware(directory: Path):
    archive = directory / "NXmini_FW_v01.10.zip"
    binary = directory / "DATANXmini.bin"
    if binary.exists() and sha256(binary.read_bytes()) == baseline.FIRMWARE_SHA256:
        return {"file": str(binary.resolve()), "sha256": baseline.FIRMWARE_SHA256, "downloaded": False}
    if archive.exists():
        data = archive.read_bytes()
    else:
        with urllib.request.urlopen(baseline.FIRMWARE_URL, timeout=45) as source:
            data = source.read(80 * 1024 * 1024 + 1)
        if sha256(data) != baseline.ZIP_SHA256:
            raise FirmwareError("Official ZIP hash does not match the pinned baseline")
        new_output(archive, data)
    if sha256(data) != baseline.ZIP_SHA256:
        raise FirmwareError("Local ZIP hash mismatch")
    import io
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        info = source.getinfo("DATANXmini.bin")
        if info.file_size != baseline.FIRMWARE_SIZE:
            raise FirmwareError("Unexpected firmware ZIP size")
        firmware = source.read(info)
    if sha256(firmware) != baseline.FIRMWARE_SHA256:
        raise FirmwareError("Official firmware hash mismatch")
    new_output(binary, firmware)
    return {"file": str(binary.resolve()), "sha256": baseline.FIRMWARE_SHA256, "downloaded": True}


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "fetch":
            result = fetch_firmware(args.directory)
        elif args.command in ("inspect", "verify"):
            report = Firmware.read(args.firmware).report(deep=args.deep)
            if args.json_output:
                write_json(args.json_output, report)
            result = report if args.command == "inspect" else {k: report[k] for k in ("valid", "official_baseline", "sha256", "flash_ready", "checksums")}
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if report["valid"] else 2
        elif args.command == "extract":
            report = Firmware.read(args.firmware).extract(args.output)
            result = {"output": str(args.output.resolve()), "sections": len(report["sections"]), "resources": len(report["resources"])}
        elif args.command == "analyze":
            from .analysis import analyze
            result = analyze(Firmware.read(args.firmware), args.output)
        elif args.command == "repack":
            data = rebuild(args.directory)
            new_output(args.output, data)
            result = {"sha256": sha256(data), "flash_ready": False, "identical_to_official": sha256(data) == baseline.FIRMWARE_SHA256}
            write_json(Path(str(args.output) + ".json"), result)
        elif args.command in ("decode", "encode"):
            operation = relc.decode if args.command == "decode" else relc.encode
            output = operation(args.input.read_bytes())
            new_output(args.output, output)
            result = {"bytes": len(output), "sha256": sha256(output)}
        elif args.command == "patch":
            from .patching import apply
            data, receipt = apply(args.firmware.read_bytes(), json.loads(args.manifest.read_text(encoding="utf-8")), reverse=args.reverse)
            new_output(args.output, data)
            write_json(Path(str(args.output) + ".receipt.json"), receipt)
            result = receipt
        elif args.command == "disasm":
            from .analysis import disassemble
            text = disassemble(args.input.read_bytes(), args.offset, args.size, args.base, args.mode)
            if args.output:
                new_output(args.output, text.encode())
                result = {"output": str(args.output.resolve())}
            else:
                print(text, end="")
                return 0
        elif args.command == "serve":
            from .receiver import serve
            serve(args)
            return 0
        elif args.command == "style":
            from .styles import render
            result = render(args.input, args.profile, args.output)
        elif args.command == "style-export":
            from .styles import export_cube
            result = export_cube(args.profile, args.output, args.size)
        elif args.command == "pairing-template":
            import re
            import uuid
            if not re.fullmatch(r"(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}", args.mac) or any(c in args.name for c in "\r\n"):
                raise ValueError("Invalid MAC address or server name")
            identity = str(uuid.UUID((args.state / "server-uuid").read_text(encoding="ascii").strip()))
            text = f"MacAddr={args.mac.upper()}\r\nUDN=uuid:{identity}\r\nfriendlyName={args.name}\r\nWOLSupport=1\r\nServerFlag=1\r\n"
            new_output(args.output, text.encode("utf-8"))
            result = {"output": str(args.output.resolve()), "camera_written": False,
                      "path_observed_in_firmware": "I:/dlna_web_root/SAMSUNGAutoBackupDESC.ini",
                      "drive_mapping_verified": False}
        elif args.command == "trace":
            from .traces import inventory
            result = inventory(args.input, args.time_column)
            if args.json_output:
                write_json(args.json_output, result)
        elif args.command in ("discover", "describe"):
            from .discovery import describe, discover
            result = discover(args.interface, args.timeout) if args.command == "discover" else describe(args.url)
            if args.json_output:
                write_json(args.json_output, result)
        else:
            raise FirmwareError("Unknown command")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (FirmwareError, OSError, KeyError, json.JSONDecodeError, ValueError) as error:
        print(f"nxmini: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
