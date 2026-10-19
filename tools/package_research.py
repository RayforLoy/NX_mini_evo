"""Bundle source, evidence summaries and the verified APK; exclude firmware and keys."""
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "dist/nxmini-evo-0.1.0-research.zip"


def main():
    files = [ROOT / name for name in ("README.md", "pyproject.toml", ".gitignore", ".dockerignore")]
    for folder in ("nxmini", "tests", "tools", "docs", "research", "profiles", "patches", "experiments", "deploy", "android"):
        for path in (ROOT / folder).rglob("*"):
            if not path.is_file() or any(part in ("local", "build", "__pycache__", ".gradle") for part in path.relative_to(ROOT).parts):
                continue
            if path.name == "local.properties" or path.name == ".env":
                continue
            files.append(path)
    apk = ROOT / "android/app/build/outputs/apk/debug/nxmini-bridge-debug.apk"
    build_report = apk.with_suffix(".build.json")
    report = json.loads((ROOT / "research/validation.json").read_text(encoding="utf-8"))
    if hashlib.sha256(apk.read_bytes()).hexdigest() != report["android"]["sha256"]:
        raise ValueError("APK differs from validation report")
    files.extend([apk, build_report])
    manifest = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(files)}
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUTPUT, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, str(path.relative_to(ROOT)).replace("\\", "/"))
        archive.writestr("BUNDLE-MANIFEST.json", json.dumps({"flash_ready": False, "files": manifest}, indent=2) + "\n")
    with zipfile.ZipFile(OUTPUT) as archive:
        assert archive.testzip() is None
        for name, expected in manifest.items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == expected
    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    OUTPUT.with_suffix(".zip.sha256").write_text(digest + "  " + OUTPUT.name + "\n", encoding="ascii")
    print(json.dumps({"bundle": str(OUTPUT), "bytes": OUTPUT.stat().st_size, "files": len(files),
                      "sha256": digest, "flash_ready": False}))


if __name__ == "__main__":
    main()
