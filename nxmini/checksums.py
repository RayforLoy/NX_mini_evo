"""Checksums observed in NX mini 1.10; these are not authenticity signatures."""

from array import array
import struct
import sys

from .errors import FirmwareError


def word_sum(data: bytes, bits: int) -> int:
    width = bits // 8
    if bits not in (16, 32) or len(data) % width:
        raise FirmwareError(f"Unaligned {bits}-bit checksum input")
    words = array("H" if bits == 16 else "I")
    if words.itemsize != width:
        raise FirmwareError("Unsupported native integer size")
    words.frombytes(data)
    if sys.byteorder != "little":
        words.byteswap()
    return sum(words) & ((1 << bits) - 1)


def inspect(data: bytes, code_offset: int, code_size: int, footer_offset: int) -> dict:
    if code_size < 2 or code_size % 2 or footer_offset % 4:
        raise FirmwareError("Invalid checksum region alignment")
    if footer_offset + 20 != len(data):
        raise FirmwareError("Invalid firmware footer position")
    stored_full, stored_size = struct.unpack_from("<II", data, footer_offset)
    code_end = code_offset + code_size
    stored_code = struct.unpack_from("<H", data, code_end - 2)[0]
    code_sum = word_sum(data[code_offset:code_end], 16)
    full_sum = word_sum(data[:footer_offset], 32)
    return {
        "code": {"algorithm": "sum-le16-zero", "offset": code_offset, "size": code_size,
                 "stored": stored_code, "sum": code_sum, "valid": code_sum == 0},
        "file": {"algorithm": "sum-le32", "offset": 0, "size": footer_offset,
                 "stored": stored_full, "calculated": full_sum, "valid": stored_full == full_sum},
        "footer_size": {"stored": stored_size, "expected": footer_offset,
                        "valid": stored_size == footer_offset},
        "footer_uninterpreted": data[footer_offset + 8:].hex(),
    }


def repair(data: bytes, code_offset: int, code_size: int, footer_offset: int) -> bytes:
    # Validate boundaries before writing, but a checksum failure is expected here.
    inspect(data, code_offset, code_size, footer_offset)
    output = bytearray(data)
    code_end = code_offset + code_size
    value = (-word_sum(output[code_offset:code_end - 2], 16)) & 65535
    struct.pack_into("<H", output, code_end - 2, value)
    struct.pack_into("<I", output, footer_offset, word_sum(output[:footer_offset], 32))
    return bytes(output)
