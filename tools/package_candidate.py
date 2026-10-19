"""Package an offline candidate with its manifest and reproducible tools."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=("C01", "C02", "C03", "C04", "C05", "C06"), default="C06")
    parser.add_argument("--output", type=Path, help="New archive path; existing files are never overwritten")
    args = parser.parse_args()
    candidate_id = args.candidate
    archive_name = (f"nxmini-evo-{candidate_id}-offline-candidate-r2.zip" if candidate_id in ("C01", "C02")
                    else f"nxmini-evo-{candidate_id}-offline-candidate.zip")
    output = args.output.resolve() if args.output else ROOT / "dist" / archive_name
    verification = json.loads((ROOT / f"research/generated/manual-lens-{candidate_id.lower()}.json").read_text())
    validation = json.loads((ROOT / "research/validation.json").read_text())
    image = ROOT / f"dist/nxmini-evo-{candidate_id}/manual-lens-{candidate_id}_OFFLINE_ONLY.bin"
    if hashlib.sha256(image.read_bytes()).hexdigest() != verification["output_sha256"]:
        raise ValueError(candidate_id + " image differs from the verification record")
    tests = validation["pytest"]
    if tests["failures"] or tests["errors"] or tests["skipped"]:
        raise ValueError("Candidate requires a complete passing local test run")
    files = [ROOT / "README.md", ROOT / "pyproject.toml", image]
    for folder in ("nxmini", "tests", "tools", "docs", "research", "profiles", "patches", "experiments"):
        for path in (ROOT / folder).rglob("*"):
            if path.is_file() and not any(p in ("local", "__pycache__") for p in path.relative_to(ROOT).parts):
                files.append(path)
    hashes = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(files)}
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.write(path, path.relative_to(ROOT).as_posix())
        archive.writestr(candidate_id + "-START-HERE.txt", (
            f"{candidate_id} OFFLINE CANDIDATE. NOT FLASH READY.\n"
            f"Read docs/candidate-{candidate_id}.md for scope, SHA-256, verification and hardware test gates.\n"
            f"Run tools/verify_candidate.py --candidate dist/nxmini-evo-{candidate_id}/manual-lens-{candidate_id}_OFFLINE_ONLY.bin --manifest patches/manual-lens-{candidate_id.lower()}.json --output work/{candidate_id}-verification.json with the verified official original.\n"
            "Camera capture, Smart Auto exposure, recovery and selfie flash are not verified.\n"
        ))
        archive.writestr(candidate_id + "-BUNDLE-MANIFEST.json", json.dumps({
            "candidate": candidate_id, "flash_ready": False, "hardware_verified": False,
            "files": hashes,
        }, indent=2) + "\n")
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError("Candidate archive CRC verification failed")
        for name, expected in hashes.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError("Candidate archive file hash mismatch: " + name)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".zip.sha256").write_text(digest + "  " + output.name + "\n", encoding="ascii")
    print(json.dumps({"path": str(output), "bytes": output.stat().st_size,
                      "files": len(hashes), "sha256": digest, "flash_ready": False}))


if __name__ == "__main__":
    main()
