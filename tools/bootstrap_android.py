"""Install hash-pinned Google SDK build assets into tools/local (Windows x64).

Downloads and unpacks tools only; does not install an app or accept licences.
SHA-1 values are the publisher's repository2-3.xml checksums. SHA-256 values
were recorded after those checks passed and pin subsequent reproductions.
"""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / "tools/local/android-sdk"
ASSETS = [
    {"name": "platform-35_r02.zip", "sha1": "0bb560a90a7a2cbd0dd8348224d518b638fe7949",
     "sha256": "0988cacad01b38a18a47bac14a0695f246bc76c1b06c0eeb8eb0dc825ab0c8e0"},
    {"name": "build-tools_r35_windows.zip", "sha1": "af059bb67cf7786f45ee0db85e2d24985df1b4b6",
     "sha256": "5753c679a1b90bcf6fbc9945a2ce39dfb9e74f1df0831a1e1866ae5b594326f0"},
]


def main():
    SDK.mkdir(parents=True, exist_ok=True)
    for asset in ASSETS:
        path = SDK / asset["name"]
        url = "https://dl.google.com/android/repository/" + asset["name"]
        if not path.exists():
            temporary = path.with_suffix(".partial")
            with urllib.request.urlopen(url, timeout=45) as source, temporary.open("wb") as output:
                while block := source.read(1024 * 1024):
                    output.write(block)
            verify(temporary, asset)
            temporary.rename(path)
        verify(path, asset)
        with zipfile.ZipFile(path) as archive:
            for member in archive.infolist():
                if not (SDK / member.filename).resolve().is_relative_to(SDK.resolve()):
                    raise ValueError("SDK archive path escapes destination")
            archive.extractall(SDK)
        print("Installed " + asset["name"], flush=True)
    (SDK / "versions.json").write_text(json.dumps(ASSETS, indent=2) + "\n", encoding="utf-8")


def verify(path, asset):
    for algorithm in ("sha1", "sha256"):
        with path.open("rb") as source:
            if hashlib.file_digest(source, algorithm).hexdigest() != asset[algorithm]:
                raise ValueError("SDK checksum mismatch: " + asset["name"])


if __name__ == "__main__":
    main()
