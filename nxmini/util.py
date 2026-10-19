import hashlib
import json
from pathlib import Path, PureWindowsPath

from .errors import FirmwareError


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) // boundary * boundary


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def safe_child(root: Path, name: str) -> Path:
    if not name or "\\" in name or any(c in name for c in '<>:"|?*') or any(ord(c) < 32 for c in name):
        raise FirmwareError(f"Unsafe filename: {name!r}")
    if any(part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()
           for part in name.split("/") if part):
        raise FirmwareError(f"Unsafe Windows filename: {name!r}")
    result = (root / name).resolve()
    if not result.is_relative_to(root.resolve()) or result == root.resolve():
        raise FirmwareError(f"Path escapes output directory: {name!r}")
    return result


def resource_filename(name: str) -> str:
    """Escape vendor trailing dots/spaces and device names without changing metadata.

    Percent itself is escaped first, making the mapping reversible and avoiding
    collisions between a literal escape and a vendor character.
    """
    value = name.replace("%", "%25")
    stem = value.rstrip(" .")
    tail = value[len(stem):]
    value = stem + "".join(f"%{ord(c):02X}" for c in tail)
    if PureWindowsPath(value).is_reserved():
        value = f"%{ord(value[0]):02X}" + value[1:]
    return value


def new_output(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
