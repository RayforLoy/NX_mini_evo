import random
import struct

import pytest

from nxmini.errors import FirmwareError
from nxmini.relc import decode, encode, rewrite


@pytest.mark.parametrize("data", [b"", b"A", b"abc", b"x" * 32768,
                                       b"abc123" * 11000,
                                       random.Random(17).randbytes(70000)],
                         ids=["empty", "one-byte", "short", "overlap", "multiple-ranges", "random"])
def test_roundtrip_raw_and_compressed(data):
    assert decode(encode(data, compress=False)) == data
    assert decode(encode(data)) == data


def test_golden_overlapping_copy():
    # Literal 'a', then 7 bytes at distance 1, then EOB.
    payload = b"\x60\x00a\x04\x01\x00\x00"
    assert decode(struct.pack(">H", len(payload)) + payload + b"\0\0") == b"a" * 8


def test_golden_extended_length():
    payload = b"\x60\x00z\x07\x01\xff\x00\x00\x00"
    assert decode(struct.pack(">H", len(payload)) + payload + b"\0\0") == b"z" * 266


@pytest.mark.parametrize("stream", [b"", b"\0", b"\x80\x02a\0\0", b"\0\x04\x80\0\0\x01\0\0",
                                         b"\0\x04\x80\0\x07\x01\0\0", b"\0\0junk",
                                         b"\0\x03\0\0a\0\0"])
def test_reject_malformed(stream):
    with pytest.raises(FirmwareError):
        decode(stream)


def test_output_limit():
    with pytest.raises(FirmwareError, match="limit"):
        decode(encode(b"a" * 2000), max_output=32)


def test_rewrite_updates_dependent_history():
    data = b"a" * 32768 + b"a" * 24576 + b"a" * 100
    original = encode(data)
    changed = data[:32767] + b"b" + data[32768:]
    assert decode(rewrite(original, changed)) == changed
    assert rewrite(original, data) == original
    with pytest.raises(FirmwareError):
        rewrite(original, data + b"x")


def test_token_preservation_keeps_unreferenced_changed_history():
    # Raw history abcde; second range copies cde at distance 3.
    payload = b"\xc0\x00\x00\x03\x00\x00"
    original = b"\x80\x05abcde" + struct.pack(">H", len(payload)) + payload + b"\0\0"
    result = rewrite(original, b"Zbcdecde", preserve_tokens=True)
    assert decode(result) == b"Zbcdecde"
    assert result[7:] == original[7:]


def test_token_preservation_repairs_referenced_history():
    # Copy abcde at distance 5, after the first history byte was changed.
    payload = b"\xc0\x00\x02\x05\x00\x00"
    original = b"\x80\x05abcde" + struct.pack(">H", len(payload)) + payload + b"\0\0"
    result = rewrite(original, b"Zbcdeabcde", preserve_tokens=True)
    assert decode(result) == b"Zbcdeabcde"
    assert result[7:] != original[7:]


@pytest.mark.parametrize("offset", [0, 1, 8, 19, 20])
def test_token_preservation_repairs_overlapping_extended_copy(offset):
    payload = b"\xc0\x00\x07\x01\x0a\x00\x00"  # 20 bytes at distance 1
    original = b"\x80\x01a" + struct.pack(">H", len(payload)) + payload + b"\0\0"
    changed = bytearray(b"a" * 21)
    changed[offset] = ord("b")
    result = rewrite(original, bytes(changed), preserve_tokens=True)
    assert decode(result) == changed
    assert rewrite(original, b"a" * 21, preserve_tokens=True) == original
