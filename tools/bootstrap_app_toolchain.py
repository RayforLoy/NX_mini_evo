"""Fetch a pinned, project-local Arm GNU toolchain; never changes PATH."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
NAME = "arm-gnu-toolchain-14.3.rel1-mingw-w64-x86_64-arm-none-eabi"
URL = ("https://armkeil.blob.core.windows.net/developer/Files/downloads/gnu/"
       f"14.3.rel1/binrel/{NAME}.zip")
# Published by Arm at URL + '.sha256asc'.
SHA256 = "864c0c8815857d68a1bbba2e5e2782255bb922845c71c97636004a3d74f60986"


def main():
    local = ROOT / "tools/local"
    destination = local / NAME
    executable = destination / "bin/arm-none-eabi-gcc.exe"
    if (executable.exists() and (destination/"arm-none-eabi/include/stdint.h").is_file()
            and list((destination/"lib/gcc/arm-none-eabi").glob("*/include/stddef.h"))):
        print(executable)
        return
    local.mkdir(exist_ok=True)
    archive = local / (NAME + ".zip")
    if not archive.exists():
        temporary = archive.with_suffix(".download")
        digest = hashlib.sha256()
        count = 0
        with urllib.request.urlopen(URL, timeout=60) as source, temporary.open("wb") as output:
            while block := source.read(1024 * 1024):
                output.write(block)
                digest.update(block)
                count += len(block)
        if digest.hexdigest() != SHA256:
            raise ValueError("Arm toolchain download hash mismatch")
        temporary.rename(archive)
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
            raise ValueError("Arm toolchain archive hash mismatch")
    # Only the C compiler, linker and matching support files are needed. Leave
    # large C++ libraries, multilib archives, manuals and debugger in the ZIP.
    with zipfile.ZipFile(archive) as source:
        for entry in source.infolist():
            relative = Path(entry.filename)
            if relative.is_absolute() or ".." in relative.parts or ":" in entry.filename:
                raise ValueError("Unexpected toolchain archive path")
            inside = relative.parts
            wanted = (inside and inside[0] == "bin") or (
                len(inside) > 1 and inside[0] == "libexec" and entry.file_size < 100_000_000
            ) or (len(inside) > 1 and inside[:2] in (
                ("arm-none-eabi", "bin"), ("arm-none-eabi", "include"))) or (
                len(inside) > 3 and inside[:3] == ("lib", "gcc", "arm-none-eabi")
                and (len(inside) <= 5 or inside[4] in ("include", "include-fixed"))
                and not entry.filename.endswith(".a")
            )
            if wanted:
                source.extract(entry, destination)
    if not executable.is_file():
        raise ValueError("Compiler missing after extraction")
    (destination / "nxmini-toolchain.json").write_text(json.dumps({
        "url": URL, "archive_sha256": SHA256, "version": "14.3.rel1",
        "selective_extraction": True,
    }, indent=2) + "\n", encoding="utf-8")
    print(executable)


if __name__ == "__main__":
    main()
