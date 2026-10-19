"""Fujitsu RELC stream codec, independently implemented from the public format.

Big-endian range sizes count payload bytes. Compressed ranges consist of
MSB-first flag groups and use an 8191-byte backwards distance. Overlapping
copies are intentional. This codec is for offline research, not a flasher.
"""

from collections import defaultdict, deque
from dataclasses import dataclass
import struct

from .errors import FirmwareError

WINDOW = 8191


@dataclass(frozen=True)
class Range:
    encoded_offset: int
    encoded_size: int
    decoded_offset: int
    decoded_size: int
    compressed: bool


def decode(stream: bytes, *, max_output: int = 64 * 1024 * 1024,
           with_ranges: bool = False):
    out = bytearray()
    ranges = []
    pos = 0
    while True:
        if pos + 2 > len(stream):
            raise FirmwareError("RELC stream is truncated or missing its EOF marker")
        start = pos
        header = struct.unpack_from(">H", stream, pos)[0]
        pos += 2
        if header == 0:
            if pos != len(stream):
                raise FirmwareError("Unexpected bytes after RELC EOF marker")
            result = bytes(out)
            return (result, ranges) if with_ranges else result
        raw = bool(header & 0x8000)
        size = (header & 0x7fff) or 32768
        end = pos + size
        if end > len(stream):
            raise FirmwareError("RELC range extends beyond the stream")
        initial = len(out)
        if raw:
            if len(out) + size > max_output:
                raise FirmwareError("RELC output exceeds configured limit")
            out.extend(stream[pos:end])
            pos = end
        else:
            finished = False
            while pos < end and not finished:
                if pos + 2 > end:
                    raise FirmwareError("Truncated RELC flags")
                flags = struct.unpack_from(">H", stream, pos)[0]
                pos += 2
                for bit in range(15, -1, -1):
                    if flags & (1 << bit):
                        if pos + 2 > end:
                            raise FirmwareError("Truncated RELC token")
                        high, low = stream[pos:pos + 2]
                        pos += 2
                        if high == 0 and low == 0:
                            finished = True
                            if pos != end:
                                raise FirmwareError("RELC EOB precedes end of range")
                            break
                        distance = (high >> 3) * 256 + low
                        length = 3 + (high & 7)
                        if length == 10:
                            while True:
                                if pos >= end:
                                    raise FirmwareError("Truncated RELC extended length")
                                extra = stream[pos]
                                pos += 1
                                length += extra
                                if extra != 255:
                                    break
                        if not 1 <= distance <= min(len(out), WINDOW):
                            raise FirmwareError("Invalid RELC backwards distance")
                        if len(out) + length > max_output:
                            raise FirmwareError("RELC output exceeds configured limit")
                        # A repeated pattern reproduces byte-by-byte overlapping copies.
                        pattern = out[-distance:]
                        repeats = (length + distance - 1) // distance
                        out.extend((pattern * repeats)[:length])
                    else:
                        if pos >= end:
                            raise FirmwareError("Truncated RELC literal group")
                        if len(out) >= max_output:
                            raise FirmwareError("RELC output exceeds configured limit")
                        out.append(stream[pos])
                        pos += 1
            if not finished:
                raise FirmwareError("Compressed RELC range has no EOB marker")
        limit = 32768 if not ranges else 24576
        if len(out) - initial > limit:
            raise FirmwareError("RELC range exceeds hardware decoded block size")
        ranges.append(Range(start, pos - start, initial, len(out) - initial, not raw))


def _compress_tokens(data: bytes, history: bytes = b"") -> list:
    source = history[-WINDOW:] + data
    first = len(source) - len(data)
    indexes = defaultdict(deque)
    recent = deque()

    def add(index):
        if index + 3 <= len(source):
            key = source[index:index + 3]
            indexes[key].append(index)
            recent.append((index, key))
        while recent and recent[0][0] < index - WINDOW + 1:
            old, key = recent.popleft()
            indexes[key].popleft()
            if not indexes[key]:
                del indexes[key]

    for index in range(first):
        add(index)
    pos = first
    tokens = []
    while pos < len(source):
        best_len, best_distance = 0, 0
        if pos + 3 <= len(source):
            candidates = indexes.get(source[pos:pos + 3], ())
            for candidate in list(candidates)[-64:][::-1]:
                distance = pos - candidate
                if not 1 <= distance <= WINDOW:
                    continue
                length = 3
                # source already contains the intended bytes, including overlapping runs.
                while pos + length < len(source) and source[candidate + length] == source[pos + length]:
                    length += 1
                if length > best_len:
                    best_len, best_distance = length, distance
                    if length >= 512:
                        break
        if best_len >= 3:
            length_code = min(best_len - 3, 7)
            token = bytearray([(best_distance >> 8) * 8 + length_code,
                               best_distance & 255])
            if length_code == 7:
                remaining = best_len - 10
                while remaining >= 255:
                    token.append(255)
                    remaining -= 255
                token.append(remaining)
            tokens.append((True, bytes(token)))
            advance = best_len
        else:
            tokens.append((False, source[pos:pos + 1]))
            advance = 1
        for index in range(pos, pos + advance):
            add(index)
        pos += advance
    return tokens


def _pack_tokens(tokens: list) -> bytes:
    tokens = tokens + [(True, b"\0\0")]
    encoded = bytearray()
    for index in range(0, len(tokens), 16):
        group = tokens[index:index + 16]
        flags = sum(1 << (15 - i) for i, (token, _) in enumerate(group) if token)
        encoded.extend(struct.pack(">H", flags))
        encoded.extend(b"".join(value for _, value in group))
    return bytes(encoded)


def _compress_payload(data: bytes, history: bytes = b"") -> bytes:
    return _pack_tokens(_compress_tokens(data, history))


def _repair_range(original: bytes, desired: bytes, history: bytes) -> bytes:
    """Preserve valid original tokens; repair their output against new history.

    The caller has already validated the complete original stream with decode().
    A changed history does not require recompressing a block if none of its copy
    tokens actually refers to changed bytes. Overlapping copies are checked too.
    """
    header = struct.unpack_from(">H", original)[0]
    if header & 0x8000:
        return original[:2] + desired
    source = history[-WINDOW:] + desired
    first = len(source) - len(desired)
    tokens, pos, output_pos, changed = [], 2, 0, False
    while pos < len(original):
        flags = struct.unpack_from(">H", original, pos)[0]
        pos += 2
        for bit in range(15, -1, -1):
            if not flags & (1 << bit):
                literal = desired[output_pos:output_pos + 1]
                changed |= literal != original[pos:pos + 1]
                tokens.append((False, literal))
                pos += 1
                output_pos += 1
                continue
            token_start = pos
            high, low = original[pos:pos + 2]
            pos += 2
            if high == 0 and low == 0:
                if output_pos != len(desired):
                    raise FirmwareError("RELC token repair output length differs")
                if not changed:
                    return original
                payload = _pack_tokens(tokens)
                if len(payload) > 0x7fff:
                    return encode_range(desired, history, compress=False)
                return struct.pack(">H", len(payload)) + payload
            distance, length = (high >> 3) * 256 + low, 3 + (high & 7)
            if length == 10:
                while True:
                    extra = original[pos]
                    pos += 1
                    length += extra
                    if extra != 255:
                        break
            wanted = desired[output_pos:output_pos + length]
            start = first + output_pos
            pattern = source[start - distance:start]
            predicted = (pattern * ((length + distance - 1) // distance))[:length]
            if predicted == wanted:
                tokens.append((True, original[token_start:pos]))
            else:
                changed = True
                tokens.extend(_compress_tokens(wanted, source[max(0, start - WINDOW):start]))
            output_pos += length
    raise FirmwareError("RELC token repair did not find the validated EOB")


def encode_range(data: bytes, history: bytes = b"", *, compress: bool = True) -> bytes:
    if not 1 <= len(data) <= 32768:
        raise FirmwareError("RELC range length must be 1..32768")
    if compress:
        payload = _compress_payload(data, history)
        if len(payload) < len(data) and len(payload) <= 0x7fff:
            return struct.pack(">H", len(payload)) + payload
    header = 0x8000 | (len(data) & 0x7fff)
    return struct.pack(">H", header) + data


def encode(data: bytes, *, compress: bool = True) -> bytes:
    result = bytearray()
    pos = 0
    while pos < len(data):
        size = 32768 if pos == 0 else 24576
        part = data[pos:pos + size]
        result.extend(encode_range(part, data[max(0, pos - WINDOW):pos], compress=compress))
        pos += len(part)
    result.extend(b"\0\0")
    return bytes(result)


def rewrite(original: bytes, modified: bytes, *, preserve_tokens: bool = False) -> bytes:
    """Re-encode only ranges whose output or input history has changed."""
    decoded, ranges = decode(original, with_ranges=True)
    if len(modified) != len(decoded):
        raise FirmwareError("Section resizing is not supported; relocation is not established")
    result = bytearray()
    for block in ranges:
        start = block.decoded_offset
        end = start + block.decoded_size
        history = max(0, start - WINDOW)
        if decoded[history:end] == modified[history:end]:
            result.extend(original[block.encoded_offset:block.encoded_offset + block.encoded_size])
        else:
            part, prior = modified[start:end], modified[history:start]
            recompressed = encode_range(part, prior)
            if preserve_tokens:
                old_range = original[block.encoded_offset:block.encoded_offset + block.encoded_size]
                repaired = _repair_range(old_range, part, prior)
                recompressed = repaired if repaired == old_range else min((repaired, recompressed), key=len)
            result.extend(recompressed)
    result.extend(b"\0\0")
    encoded = bytes(result)
    if decode(encoded) != modified:
        raise FirmwareError("Re-encoded RELC section failed independent decode comparison")
    return encoded
