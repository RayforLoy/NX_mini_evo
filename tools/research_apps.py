"""Reproduce original-file/canvas probes and the non-executable sizing study."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nxmini import baseline, relc
from nxmini.app_platform_probe import NativeFileProbe, NativeCanvasProbe
from nxmini.firmware import Firmware
from nxmini.util import align, sha256, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build",type=Path,required=True)
    parser.add_argument("--firmware",type=Path,default=ROOT/"data/firmware/DATANXmini.bin")
    parser.add_argument("--output",type=Path,default=ROOT/"research/generated")
    args = parser.parse_args()
    f = Firmware.read(args.firmware)
    if sha256(f.data) != baseline.FIRMWARE_SHA256:
        raise ValueError("Research requires the pinned original 1.10 firmware")
    section = f.sections[0]
    original = f.section_bytes(section)
    decoded = relc.decode(original)
    args.output.mkdir(parents=True,exist_ok=True)
    write_json(args.output/"custom-app-file-wrappers.json",NativeFileProbe(decoded).evidence())
    write_json(args.output/"custom-app-canvas.json",NativeCanvasProbe(decoded).evidence())
    blob = (args.build/"loader.bin").read_bytes()
    offset = 0x804eb0
    if any(decoded[offset:offset+len(blob)]):
        raise ValueError("Sizing-study zero preimage differs")
    modified = decoded[:offset]+blob+decoded[offset+len(blob):]
    rows = []
    for preserve in (False,True):
        encoded = relc.rewrite(original,modified,preserve_tokens=preserve)
        if relc.decode(encoded) != modified:
            raise ValueError("Sizing-study RELC roundtrip failed")
        capacity = align(section.offset+section.size,512)-section.offset
        rows.append({"preserve_tokens":preserve,"original_compressed_bytes":len(original),
                     "encoded_bytes":len(encoded),"compressed_growth_bytes":len(encoded)-len(original),
                     "slot_capacity_bytes":capacity,"baseline_padding_bytes":capacity-len(original),
                     "fits_fixed_slot":len(encoded)<=capacity,"decoded_roundtrip_verified":True})
    report = {"schema_version":1,"firmware_sha256":sha256(f.data),
              "experiment":"sizing-only copy of loader into an unproven zero area; no launch hook",
              "loader_image_sha256":sha256(blob),"test_decoded_offset":hex(offset),
              "loader_bytes":len(blob),"zero_area_runtime_ownership_verified":False,"runs":rows,
              "firmware_binary_written":False,"camera_modified":False,"flash_ready":False}
    write_json(args.output/"custom-app-loader-size.json",report)
    print({"wrapper_and_canvas_probes":True,"sizing":rows,"firmware_binary_written":False})


if __name__ == "__main__":
    main()
