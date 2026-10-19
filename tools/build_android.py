"""Build and verify the Java-only Android app with locally installed SDK tools.

Does not install an app, accept SDK licences, or access any connected device.
The normal Android Studio Gradle project is provided alongside this build path.
"""
import json
import hashlib
import os
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "tools" / "local"
SDK = LOCAL / "android-sdk"
APP = ROOT / "android" / "app"
BUILD = APP / "build" / "standalone"


def run(args):
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT)


def main():
    android_jar = next(SDK.rglob("android.jar"))
    aapt = next(SDK.rglob("aapt2.exe"))
    tools = aapt.parent
    jdk = next(LOCAL.glob("jdk-*/bin/javac.exe")).parent
    os.environ["JAVA_HOME"] = str(jdk.parent)
    os.environ["PATH"] = str(jdk) + os.pathsep + os.environ["PATH"]
    for name in ("classes", "dex", "generated"):
        (BUILD / name).mkdir(parents=True, exist_ok=True)
    resource_zip = BUILD / "resources.zip"
    unsigned = BUILD / "unsigned.apk"
    run([aapt, "compile", "--dir", APP / "src/main/res", "-o", resource_zip])
    run([aapt, "link", "-I", android_jar, "--manifest", APP / "src/main/AndroidManifest.xml",
         "--java", BUILD / "generated", "--version-code", "1", "--version-name", "0.1.0-research",
         "-o", unsigned, resource_zip])
    java = list((APP / "src/main/java").rglob("*.java"))
    run([jdk / "javac.exe", "-encoding", "UTF-8", "-source", "8", "-target", "8", "-Xlint:-options",
         "-classpath", android_jar, "-d", BUILD / "classes", *java])
    run([tools / "d8.bat", "--lib", android_jar, "--min-api", "29", "--output", BUILD / "dex",
         *sorted((BUILD / "classes").rglob("*.class"))])
    with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(BUILD / "dex/classes.dex", "classes.dex")
    aligned = BUILD / "aligned.apk"
    run([tools / "zipalign.exe", "-f", "4", unsigned, aligned])
    key = LOCAL / "nxmini-debug.keystore"
    if not key.exists():
        run([jdk / "keytool.exe", "-genkeypair", "-keystore", key, "-alias", "androiddebugkey",
             "-storepass", "android", "-keypass", "android", "-dname", "CN=NX mini evo local debug",
             "-keyalg", "RSA", "-keysize", "2048", "-validity", "10000"])
    output = APP / "build/outputs/apk/debug/nxmini-bridge-debug.apk"
    output.parent.mkdir(parents=True, exist_ok=True)
    run([tools / "apksigner.bat", "sign", "--ks", key, "--ks-key-alias", "androiddebugkey",
         "--ks-pass", "pass:android", "--key-pass", "pass:android", "--out", output, aligned])
    run([tools / "apksigner.bat", "verify", "--verbose", output])
    run([aapt, "dump", "badging", output])
    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    report = {"apk": str(output.relative_to(ROOT)), "sha256": digest(output),
              "build_succeeded": True, "apksigner_verify_succeeded": True,
              "device_tested": False, "min_sdk": 29, "target_sdk": 35,
              "source_sha256": {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path)
                                for path in java + [APP / "src/main/AndroidManifest.xml"]}}
    output.with_suffix(".build.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
