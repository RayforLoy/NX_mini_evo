"""NXA1 trusted native application packages, independent of firmware images."""
from dataclasses import dataclass
import re
import struct
import zlib

from .baseline import FIRMWARE_SHA256
from .errors import FirmwareError

HEADER_BYTES = 128
IMAGE_LIMIT = 128 * 1024
BSS_LIMIT = 64 * 1024
RELOC_LIMIT = 4096
HEADER = struct.Struct("<4sHH10I32s32s16s")


@dataclass(frozen=True)
class AppPackage:
    image: bytes
    bss_bytes: int
    entry_offset: int
    relocations: tuple[int, ...]
    state_bytes: int
    stack_bytes: int
    app_id: str
    profile_sha256: str

    @classmethod
    def read(cls, data: bytes, *, profile=FIRMWARE_SHA256):
        if len(data) < HEADER_BYTES:
            raise FirmwareError("NXA header truncated")
        (magic, version, header_bytes, abi, arch, flags, image_bytes,
         bss_bytes, entry, count, state, stack, crc, fingerprint, app_id, reserved) = HEADER.unpack_from(data)
        if (magic, version, header_bytes) != (b"NXA1", 1, HEADER_BYTES) or any(reserved):
            raise FirmwareError("NXA header/version/reserved mismatch")
        if (abi, arch, flags) != (1, 1, 0):
            raise FirmwareError("NXA ABI/architecture/flags unsupported")
        if not (4 <= image_bytes <= IMAGE_LIMIT and bss_bytes <= BSS_LIMIT
                and count <= RELOC_LIMIT and 0 < state <= 8192
                and 1024 <= stack <= 32768 and stack % 8 == 0
                and entry % 4 == 0 and entry <= image_bytes - 4):
            raise FirmwareError("NXA resource or entry limit exceeded")
        if len(data) != HEADER_BYTES + image_bytes + count * 4:
            raise FirmwareError("NXA exact file size mismatch")
        if fingerprint.hex() != profile:
            raise FirmwareError("NXA firmware adapter profile mismatch")
        if not re.fullmatch(rb"[a-z0-9_-]{1,31}\x00+", app_id):
            raise FirmwareError("NXA application ID invalid")
        if zlib.crc32(data[:44] + b"\0"*4 + data[48:]) != crc:
            raise FirmwareError("NXA CRC mismatch")
        image = data[HEADER_BYTES:HEADER_BYTES+image_bytes]
        relocations = tuple(v[0] for v in struct.iter_unpack("<I", data[HEADER_BYTES+image_bytes:]))
        previous = -1
        for off in relocations:
            if off % 4 or off > image_bytes - 4 or off <= previous:
                raise FirmwareError("NXA relocation is unaligned, duplicate or outside image")
            if struct.unpack_from("<I", image, off)[0] >= image_bytes + bss_bytes:
                raise FirmwareError("NXA relocated pointer leaves module memory")
            previous = off
        return cls(image, bss_bytes, entry, relocations, state, stack,
                   app_id.rstrip(b"\0").decode("ascii"), profile)

    def encode(self):
        name = self.app_id.encode("ascii")
        if not re.fullmatch(rb"[a-z0-9_-]{1,31}", name):
            raise FirmwareError("NXA application ID invalid")
        header = HEADER.pack(b"NXA1", 1, HEADER_BYTES, 1, 1, 0, len(self.image),
                             self.bss_bytes, self.entry_offset, len(self.relocations),
                             self.state_bytes, self.stack_bytes, 0,
                             bytes.fromhex(self.profile_sha256), name.ljust(32,b"\0"), b"\0"*16)
        data = bytearray(header + self.image + b"".join(struct.pack("<I", v) for v in self.relocations))
        struct.pack_into("<I", data, 44, zlib.crc32(data))
        self.read(bytes(data), profile=self.profile_sha256)
        return bytes(data)

    def relocate(self, base):
        if not base or base % 8 or base + len(self.image) + self.bss_bytes > 0xffffffff:
            raise FirmwareError("NXA load address invalid")
        memory = bytearray(self.image + b"\0"*self.bss_bytes)
        for off in self.relocations:
            struct.pack_into("<I", memory, off, struct.unpack_from("<I", memory, off)[0] + base)
        return bytes(memory)


def elf_image(data: bytes):
    """Read our linked ELF32 ARM image and retained, resolved relocation table.

    Only internal ABS32 pointers need rebasing. Relative branches/data remain
    unchanged. Reject imports and unimplemented relocations instead of guessing.
    """
    if len(data) < 52 or data[:7] != b"\x7fELF\x01\x01\x01":
        raise FirmwareError("Expected little endian ELF32")
    header = struct.unpack_from("<HH5I6H", data, 16)
    kind, machine, _, entry, _, shoff, flags, _, _, _, shsize, shnum, shstr = header
    if (kind != 2 or machine != 40 or flags != 0x05000200 or
            shsize != 40 or shnum == 0 or shstr >= shnum):
        raise FirmwareError("Expected linked ARM executable with section table")
    if shoff + shnum*40 > len(data):
        raise FirmwareError("ELF section table truncated")
    sections = [struct.unpack_from("<10I", data, shoff+i*40) for i in range(shnum)]
    for s in sections:
        if s[1] != 8 and s[4]+s[5] > len(data):
            raise FirmwareError("ELF section outside file")
    names_section = sections[shstr]
    names = data[names_section[4]:names_section[4]+names_section[5]]
    symbols = {}
    symbol_tables = {}
    for index, s in enumerate(sections):
        if s[1] != 2:
            continue
        if s[9] != 16 or s[5] % 16 or s[6] >= shnum:
            raise FirmwareError("ELF symbol table malformed")
        strings = sections[s[6]]
        strings = data[strings[4]:strings[4]+strings[5]]
        table = []
        for pos in range(s[4],s[4]+s[5],16):
            name, value, size, info, other, shndx = struct.unpack_from("<IIIBBH",data,pos)
            end = strings.find(b"\0",name)
            if end < name:
                raise FirmwareError("ELF symbol name invalid")
            text = strings[name:end].decode("ascii")
            if shndx == 0 and text and info >> 4 in (1,2):
                raise FirmwareError(f"ELF import unsupported: {text}")
            table.append((value,shndx))
            if text:
                symbols[text] = value
        symbol_tables[index] = table
    image_end, memory_end = symbols.get("__image_end",0), symbols.get("__memory_end",0)
    if not 4 <= image_end <= IMAGE_LIMIT or not image_end <= memory_end <= image_end+BSS_LIMIT:
        raise FirmwareError("ELF module bounds invalid")
    image = bytearray(image_end)
    for s in sections:
        if not s[2] & 2 or s[1] == 8 or not s[5]:
            continue
        if s[1] != 1 or s[3]+s[5] > image_end:
            raise FirmwareError("ELF unexpected allocated section")
        image[s[3]:s[3]+s[5]] = data[s[4]:s[4]+s[5]]
    relocations = []
    for s in sections:
        if s[1] != 9:
            continue
        if s[9] != 8 or s[5] % 8 or s[6] not in symbol_tables or s[7] >= shnum:
            raise FirmwareError("ELF relocation table malformed")
        if not sections[s[7]][2] & 2:
            continue
        for pos in range(s[4],s[4]+s[5],8):
            off, info = struct.unpack_from("<II",data,pos)
            relocation, sym = info & 255, info >> 8
            table = symbol_tables[s[6]]
            if sym >= len(table) or table[sym][1] in (0,0xfff1) or off > image_end - 4:
                raise FirmwareError("ELF relocation references external/absolute symbol")
            if relocation == 2:  # R_ARM_ABS32
                relocations.append(off)
            elif relocation not in (3,28,29,40):  # REL32, CALL, JUMP24, V4BX
                raise FirmwareError(f"ELF relocation not implemented: {relocation}")
    if len(set(relocations)) != len(relocations):
        raise FirmwareError("Duplicate ELF ABS32 relocation")
    return bytes(image), memory_end-image_end, entry, tuple(sorted(relocations)), symbols
