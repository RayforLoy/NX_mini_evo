"""Record observed test, roundtrip and Android build results, never camera claims."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import xml.etree.ElementTree as ET

from nxmini import baseline
from nxmini.util import write_json

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def identical(first, second):
    if first.stat().st_size != second.stat().st_size:
        return False
    with first.open("rb") as a, second.open("rb") as b:
        while block := a.read(1024 * 1024):
            if block != b.read(1024 * 1024):
                return False
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, default=ROOT / "work/test-results.xml")
    parser.add_argument("--roundtrip", type=Path, default=ROOT / "work/final-roundtrip.bin")
    args = parser.parse_args()
    args.junit = args.junit.resolve()
    args.roundtrip = args.roundtrip.resolve()
    root = ET.parse(args.junit).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {key: sum(int(s.attrib.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    original = ROOT / "data/firmware/DATANXmini.bin"
    build_path = ROOT / "android/app/build/outputs/apk/debug/nxmini-bridge-debug.build.json"
    build = json.loads(build_path.read_text(encoding="utf-8"))
    apk = ROOT / build["apk"]
    firmware = json.loads((ROOT / "research/generated/firmware.json").read_text(encoding="utf-8"))
    if digest(original) != baseline.FIRMWARE_SHA256 or firmware["sha256"] != baseline.FIRMWARE_SHA256:
        raise ValueError("Original firmware differs from the verified baseline")
    if digest(apk) != build["sha256"]:
        raise ValueError("APK changed after signature verification")
    for path, expected in build["source_sha256"].items():
        if digest(ROOT / path) != expected:
            raise ValueError("Android source changed after build: " + path)
    equal = identical(original, args.roundtrip)
    if not equal or totals["failures"] or totals["errors"]:
        raise ValueError("Roundtrip or tests did not pass")
    tracked_sources = sorted(p for folder in ("nxmini", "tests", "tools", "profiles", "patches", "experiments")
                             for p in (ROOT / folder).rglob("*")
                             if p.suffix in (".py", ".java", ".ps1", ".json") and "local" not in p.parts)
    source_hash = hashlib.sha256()
    for path in tracked_sources:
        source_hash.update(str(path.relative_to(ROOT)).replace("\\", "/").encode())
        source_hash.update(bytes.fromhex(digest(path)))
    compose_parsed = False
    try:
        import yaml
        compose = yaml.safe_load((ROOT / "deploy/compose.yaml").read_text(encoding="utf-8"))
        compose_parsed = compose["services"]["nxmini-receiver"]["network_mode"] == "host"
    except ImportError:
        pass
    report = {
        "schema_version": 1, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "platform": platform.platform(), "python": platform.python_version(),
        "source_tree_sha256": source_hash.hexdigest(),
        "pytest": {**totals, "seconds": sum(float(s.attrib.get("time", 0)) for s in suites),
                   "official_firmware_tests_present": sum(1 for c in root.iter("testcase") if c.attrib.get("name", "").startswith("test_official_")),
                   "junit": str(args.junit.relative_to(ROOT)).replace("\\", "/")},
        "firmware": {"sha256": firmware["sha256"], "bytes": firmware["bytes"],
                     "sections": len(firmware["sections"]), "resources": len(firmware["resources"]),
                     "decoded_bytes": sum(s["decoded_bytes"] for s in firmware["sections"]),
                     "checksums": firmware["checksums"], "unchanged_rebuild_byte_identical": equal},
        "android": build,
        "container": {"configuration_present": True, "yaml_parse_passed": compose_parsed,
                      "engine_available": False, "build_verified": False, "nas_deployed": False},
        "hardware": {"camera_connected": None, "camera_connection_checked_during_this_validation": False,
                     "camera_patch_tested": False,
                     "camera_transfer_tested": False, "phone_tested": False,
                     "nx_m_electrical_capture_available": False, "recovery_verified": False},
        "flash_ready": False,
    }
    experiment = ROOT / "research/generated/flash-qualification-experiment.json"
    if experiment.exists():
        report["firmware_experiment"] = json.loads(experiment.read_text(encoding="utf-8"))
    feedback = ROOT / "research/generated/c05-hardware-feedback-20261019.json"
    if feedback.exists():
        report["hardware"]["partial_C05_test_reported_by_user"] = json.loads(feedback.read_text(encoding="utf-8"))
        report["hardware"]["camera_patch_tested_meaning"] = "complete hardware validation remains false; partial user observations are recorded separately"
    for candidate_id in ("C01", "C02", "C03", "C04", "C05", "C06"):
        candidate_report = ROOT / f"research/generated/manual-lens-{candidate_id.lower()}.json"
        if candidate_report.exists():
            candidate = json.loads(candidate_report.read_text(encoding="utf-8"))
            image = ROOT / f"dist/nxmini-evo-{candidate_id}/manual-lens-{candidate_id}_OFFLINE_ONLY.bin"
            if digest(image) != candidate["output_sha256"]:
                raise ValueError(candidate_id + " candidate differs from its verification report")
            report["firmware_candidate_" + candidate_id] = candidate
    write_json(ROOT / "research/validation.json", report)
    print(json.dumps({"tests": totals, "roundtrip_identical": equal,
                      "apk_verified": True, "flash_ready": False}))


if __name__ == "__main__":
    main()
