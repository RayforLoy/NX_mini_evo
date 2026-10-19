from dataclasses import asdict, dataclass
from pathlib import Path
import re
import struct

from . import baseline, checksums, relc
from .errors import FirmwareError
from .util import align, new_output, resource_filename, safe_child, sha256, write_json


@dataclass(frozen=True)
class Section:
    number: int
    offset: int
    size: int


@dataclass(frozen=True)
class Resource:
    name: str
    offset: int
    size: int


class Firmware:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 0x400:
            raise FirmwareError("Truncated M7MU header")
        block_size, writer_size, writer_entry, sdram_size, nand_size = struct.unpack_from("<5I", data)
        code_size, code_offset = struct.unpack_from("<II", data, 0x185)
        text = lambda offset, size: bytes(b & 0x7f for b in data[offset:offset + size]).rstrip(b"\0").decode("ascii")
        try:
            version, build = text(0x18d, 5), text(0x192, 12)
            version2, model = text(0x19e, 11), text(0x1a9, 9)
        except UnicodeDecodeError as error:
            raise FirmwareError("Invalid M7MU metadata") from error
        if model != baseline.MODEL:
            raise FirmwareError(f"Unsupported model {model!r}; expected NXMINI")
        if block_size != 0x400 or code_offset != block_size + writer_size:
            raise FirmwareError("Invalid M7MU header/writer boundaries")
        if code_offset % 512 or code_size < 4 or code_offset + code_size > len(data):
            raise FirmwareError("Invalid M7MU code region")
        self.footer_offset = struct.unpack_from("<I", data, 0x256)[0]
        if self.footer_offset + 20 != len(data) or code_offset + code_size > self.footer_offset:
            raise FirmwareError("Firmware size field does not match file length")
        table = struct.unpack_from("<25I", data, 0x1b2)
        count = table[0]
        if not 1 <= count <= 12:
            raise FirmwareError("Invalid M7MU section count")
        self.sections = []
        offset = code_offset
        numbers = set()
        for i in range(count):
            number, size = table[1 + 2 * i:3 + 2 * i]
            if number in numbers or number == 0 or size < 2:
                raise FirmwareError("Duplicate section number or invalid section length")
            if offset + size > code_offset + code_size - 2:
                raise FirmwareError("Section extends beyond code payload")
            self.sections.append(Section(number, offset, size))
            numbers.add(number)
            offset = align(offset + size, 512)
        self.resource_offset = align(offset, 2048)
        if self.resource_offset < code_offset + code_size:
            raise FirmwareError("Code region overlaps the resource directory")
        if self.resource_offset + 32 > self.footer_offset:
            raise FirmwareError("Truncated SF_RESOURCE header")
        if data[self.resource_offset:self.resource_offset + 16] != b"SF_RESOURCE\0\0\0\0\0":
            raise FirmwareError("Missing SF_RESOURCE directory")
        resource_count = struct.unpack_from("<I", data, self.resource_offset + 20)[0]
        if not 1 <= resource_count <= 10000:
            raise FirmwareError("Invalid SF_RESOURCE count")
        base = self.resource_offset + 32 + resource_count * 64
        if base > self.footer_offset:
            raise FirmwareError("Truncated SF_RESOURCE directory")
        self.resources = []
        names = set()
        intervals = []
        for i in range(resource_count):
            raw, relative, size = struct.unpack_from("<56sII", data, self.resource_offset + 32 + i * 64)
            try:
                name = raw.split(b"\0", 1)[0].decode("ascii")
            except UnicodeDecodeError as error:
                raise FirmwareError("Invalid resource name") from error
            if not name or not re.fullmatch(r"[A-Za-z0-9_. -]+", name) or name in (".", ".."):
                raise FirmwareError("Unsafe resource name")
            if name.casefold() in names:
                raise FirmwareError("Duplicate resource name")
            names.add(name.casefold())
            absolute = base + relative
            if absolute + size > self.footer_offset:
                raise FirmwareError(f"Resource {name} extends beyond firmware body")
            self.resources.append(Resource(name, absolute, size))
            if size:
                intervals.append((absolute, absolute + size))
        intervals.sort()
        if any(left[1] > right[0] for left, right in zip(intervals, intervals[1:])):
            raise FirmwareError("Overlapping resources")
        self.header = {
            "model": model, "version": version, "build": build, "version2": version2,
            "block_size": block_size, "writer_size": writer_size, "writer_entry": writer_entry,
            "sdram_parameter_size": sdram_size, "nand_parameter_size": nand_size,
            "code_offset": code_offset, "code_size": code_size,
        }

    @classmethod
    def read(cls, path):
        return cls(Path(path).read_bytes())

    def section_bytes(self, section: Section) -> bytes:
        return self.data[section.offset:section.offset + section.size]

    def resource_bytes(self, name: str) -> bytes:
        item = next((r for r in self.resources if r.name == name), None)
        if item is None:
            raise FirmwareError(f"Resource not found: {name}")
        return self.data[item.offset:item.offset + item.size]

    def report(self, *, deep: bool = False) -> dict:
        digest = sha256(self.data)
        report = {
            "schema_version": 1, "sha256": digest, "bytes": len(self.data),
            "official_baseline": digest == baseline.FIRMWARE_SHA256,
            "flash_ready": False, "header": self.header,
            "sections": [asdict(s) for s in self.sections],
            "resource_offset": self.resource_offset,
            "resources": [{**asdict(r), "extracted_name": resource_filename(r.name)} for r in self.resources],
            "checksums": checksums.inspect(self.data, self.header["code_offset"],
                                            self.header["code_size"], self.footer_offset),
        }
        if deep:
            for section, item in zip(self.sections, report["sections"]):
                decoded, ranges = relc.decode(self.section_bytes(section), with_ranges=True)
                item.update(decoded_bytes=len(decoded), decoded_sha256=sha256(decoded), ranges=len(ranges))
                if report["official_baseline"]:
                    item["reference_match"] = sha256(decoded) == baseline.DECODED_SHA256.get(section.number)
        report["valid"] = all(v["valid"] for k, v in report["checksums"].items() if isinstance(v, dict))
        report["footer_layout_preserved"] = self.data[self.footer_offset + 8:] == bytes.fromhex("fc0000000000000000000000")
        report["valid"] &= report["footer_layout_preserved"]
        if deep and report["official_baseline"]:
            report["valid"] &= all(s["reference_match"] for s in report["sections"])
        return report

    def extract(self, directory: Path) -> dict:
        if directory.exists():
            raise FirmwareError("Extraction destination already exists; choose a new directory")
        directory.mkdir(parents=True)
        # The full original is deliberately retained as the lossless reconstruction base.
        new_output(directory / "original.bin", self.data)
        new_output(directory / "header.bin", self.data[:0x400])
        new_output(directory / "writer.bin", self.data[0x400:self.header["code_offset"]])
        report = self.report(deep=True)
        for section in self.sections:
            name = f"chunk-{section.number:02d}.bin"
            compressed = self.section_bytes(section)
            new_output(directory / "sections" / name, compressed)
            new_output(directory / "decoded" / name, relc.decode(compressed))
        for resource in self.resources:
            new_output(safe_child(directory / "resources", resource_filename(resource.name)), self.resource_bytes(resource.name))
        write_json(directory / "manifest.json", report)
        return report


def rebuild(directory: Path) -> bytes:
    """Rebuild fixed-size resource edits and code edits without moving any region."""
    firmware = Firmware.read(directory / "original.bin")
    import json
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if sha256(firmware.data) != manifest["sha256"]:
        raise FirmwareError("Extraction original no longer matches manifest")
    if not firmware.report()["valid"]:
        raise FirmwareError("Cannot rebuild an extraction with invalid original checksums or footer")
    if (directory / "header.bin").read_bytes() != firmware.data[:0x400]:
        raise FirmwareError("Direct header edits are not supported")
    if (directory / "writer.bin").read_bytes() != firmware.data[0x400:firmware.header["code_offset"]]:
        raise FirmwareError("Writer modification is not supported")
    output = bytearray(firmware.data)
    for index, section in enumerate(firmware.sections):
        name = f"chunk-{section.number:02d}.bin"
        raw = firmware.section_bytes(section)
        if (directory / "sections" / name).read_bytes() != raw:
            raise FirmwareError("Edit decoded sections, not raw compressed sections")
        desired = (directory / "decoded" / name).read_bytes()
        if relc.decode(raw) == desired:
            continue
        encoded = relc.rewrite(raw, desired)
        capacity = align(section.offset + section.size, 512) - section.offset
        if index == len(firmware.sections) - 1:
            capacity = firmware.header["code_offset"] + firmware.header["code_size"] - 2 - section.offset
        if len(encoded) > capacity:
            raise FirmwareError(f"Recompressed section {section.number} exceeds existing space")
        # Padding is not assumed disposable unless the original bytes are zero.
        if len(encoded) > section.size and any(firmware.data[section.offset + section.size:section.offset + len(encoded)]):
            raise FirmwareError("Section growth would overwrite nonzero padding")
        output[section.offset:section.offset + max(section.size, len(encoded))] = encoded.ljust(max(section.size, len(encoded)), b"\0")
        struct.pack_into("<I", output, 0x1ba + index * 8, len(encoded))
    for resource in firmware.resources:
        desired = safe_child(directory / "resources", resource_filename(resource.name)).read_bytes()
        if len(desired) != resource.size:
            raise FirmwareError(f"Resource resizing is unsupported: {resource.name}")
        output[resource.offset:resource.offset + resource.size] = desired
    if output == firmware.data:
        return firmware.data
    result = checksums.repair(bytes(output), firmware.header["code_offset"],
                              firmware.header["code_size"], firmware.footer_offset)
    rebuilt = Firmware(result)
    # Section offsets must remain identical even after a compressed-size change.
    if [s.offset for s in rebuilt.sections] != [s.offset for s in firmware.sections]:
        raise FirmwareError("Recompression would relocate following sections")
    for section in rebuilt.sections:
        desired = (directory / "decoded" / f"chunk-{section.number:02d}.bin").read_bytes()
        if relc.decode(rebuilt.section_bytes(section)) != desired:
            raise FirmwareError("Rebuilt section content does not match extracted input")
    return result
