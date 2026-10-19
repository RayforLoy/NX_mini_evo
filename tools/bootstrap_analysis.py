"""Download checksum-verified, project-local Ghidra and Temurin JDK 21."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "tools" / "local"
GHIDRA = {
    "name": "ghidra_12.1.4_PUBLIC_20260921.zip",
    "url": "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.1.4_build/ghidra_12.1.4_PUBLIC_20260921.zip",
    "sha256": "ddac49f903da9d5bac833e5cc79395098b9c33cfd3279be5f31bd00387d2d4db",
}


def request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "nxmini-evo"}), timeout=45)


def install(item):
    archive = LOCAL / item["name"]
    if not archive.exists():
        part = archive.with_suffix(".partial")
        count = 0
        with request(item["url"]) as source, part.open("wb") as output:
            while block := source.read(1024 * 1024):
                output.write(block)
                count += len(block)
                if count % (32 * 1024 * 1024) == 0:
                    print(f'{item["name"]}: {count // 1048576} MiB', flush=True)
        with part.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != item["sha256"]:
            raise RuntimeError(f'Checksum mismatch: {item["name"]}')
        part.rename(archive)
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != item["sha256"]:
            raise RuntimeError(f'Checksum mismatch: {item["name"]}')
    with zipfile.ZipFile(archive) as source:
        roots = set()
        for member in source.infolist():
            target = (LOCAL / member.filename).resolve()
            if not target.is_relative_to(LOCAL.resolve()):
                raise RuntimeError("Archive path escapes tool directory")
            roots.add(member.filename.split("/")[0])
        source.extractall(LOCAL)
    print(f'Installed {item["name"]}', flush=True)
    return {**item, "directories": sorted(roots)}


def main():
    LOCAL.mkdir(parents=True, exist_ok=True)
    url = "https://api.github.com/repos/adoptium/temurin21-binaries/releases/tags/jdk-21.0.12.1%2B1"
    release = json.load(request(url))
    asset = next(a for a in release["assets"] if a["name"].startswith("OpenJDK21U-jdk_x64_windows_hotspot_") and a["name"].endswith(".zip"))
    checksum_asset = next(a for a in release["assets"] if a["name"] == asset["name"] + ".sha256.txt")
    checksum = request(checksum_asset["browser_download_url"]).read().decode().split()[0]
    jdk = {"name": asset["name"], "url": asset["browser_download_url"], "sha256": checksum}
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        installed = list(pool.map(install, [GHIDRA, jdk]))
    (LOCAL / "versions.json").write_text(json.dumps(installed, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
