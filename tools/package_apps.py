"""Build and package A01, without proprietary firmware or flashable images."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]

README = """# NX mini evo A01：自定义软件与黄金矿工

这是 ARM 软件包／加载器／电脑预览验证包，**尚未接入相机，不是可刷固件**。
黄金矿工为原创简化 C 实现；电脑实际执行同一份 ARM 二进制。

在原工作区，双击 `play-gold-miner.cmd` 即可运行。
空格／Enter 下钩或继续，↑ 炸药，P 暂停，Esc 退出。

解压到其他 Windows 电脑，需要 Python 3.12、Tk、Unicorn 和 Pillow：

```powershell
py -3.12 -m venv .venv
.\\.venv\\Scripts\\python.exe -m pip install unicorn==2.1.4 Pillow
.\\.venv\\Scripts\\python.exe tools/play_gold_miner.py --build .
```

本包包含 `gold_miner.nxa`、C 加载器与运行时 ARM ELF、完整新增源码、
构建命令、异常包／正常五关验证记录及截图。`build.json` 记录编译器和源码哈希。
运行 `tools/bootstrap_app_toolchain.py` 下载固定且校验哈希的 Arm 编译器，
再用 `tools/build_apps.py --output work/rebuild` 重建；也可用 `--source` 编译自己的 C 程序。

详细研究和当前接口边界见 `docs/custom-apps.md`。
平台档案 `firmware/apps/platform/nxmini-110.json` 保持禁用：
机身菜单／启动挂钩、可执行 RAM、CPU 缓存同步、真实屏幕和按键尚未接通。
将 `.nxa` 放到卡中不会自动执行；包中没有 `DATANXmini.bin`。
原厂函数探针与尺寸研究需要原项目中的官方 1.10 固件，不随此包分发。

应用属于受信任的原生代码；包校验不提供安全沙箱或签名认证。
第三方 Python 依赖、编译工具按其自身许可使用，未将它们打包进本 ZIP。
"""

LAUNCHER = r'''@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" tools\play_gold_miner.py --build .
) else if exist "..\..\.venv\Scripts\python.exe" (
  "..\..\.venv\Scripts\python.exe" tools\play_gold_miner.py --build .
) else (
  py -3.12 tools\play_gold_miner.py --build .
)
if errorlevel 1 pause
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path,default=ROOT/"dist/nxmini-apps-A01")
    args = parser.parse_args()
    output = args.output.resolve()
    archive = output.with_suffix(".zip")
    if output.exists() or archive.exists():
        raise SystemExit("Choose a new output; package paths already exist")
    subprocess.run([sys.executable,str(ROOT/"tools/build_apps.py"),"--output",str(output)],check=True)
    subprocess.run([sys.executable,str(ROOT/"tools/verify_apps.py"),str(output),
                    "--output",str(output/"validation.json"),"--preview",str(output/"previews")],check=True)
    files = ["nxmini/__init__.py","nxmini/errors.py","nxmini/util.py","nxmini/baseline.py",
             "nxmini/app_package.py","nxmini/app_probe.py","nxmini/app_platform_probe.py",
             "nxmini/firmware.py","nxmini/checksums.py","nxmini/relc.py",
             "tools/bootstrap_app_toolchain.py","tools/build_apps.py","tools/play_gold_miner.py",
             "tools/verify_apps.py","tools/research_apps.py","tools/package_apps.py",
             "tests/test_apps.py","docs/custom-apps.md",
             "research/generated/os-platform-evidence.json",
             "research/generated/custom-app-file-wrappers.json",
             "research/generated/custom-app-canvas.json",
             "research/generated/custom-app-loader-size.json"]
    files += [str(p.relative_to(ROOT)) for p in (ROOT/"firmware/apps").rglob("*") if p.is_file()]
    for relative in files:
        source = ROOT/relative
        dest = output/relative
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,dest)
    (output/"README.md").write_text(README,encoding="utf8")
    (output/"play-gold-miner.cmd").write_text(LAUNCHER.replace("\n","\r\n"),encoding="ascii",newline="")
    build = json.loads((output/"build.json").read_text())
    size = json.loads((output/"research/generated/custom-app-loader-size.json").read_text())
    if size["loader_image_sha256"] != build["modules"]["loader"]["sha256"]:
        raise ValueError("Sizing evidence was generated for a different loader build")
    manifest = {str(p.relative_to(output)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(output.rglob("*")) if p.is_file()}
    (output/"manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf8")
    with zipfile.ZipFile(archive,"x",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(output.rglob("*")):
            if p.is_file(): z.write(p,str(Path(output.name)/p.relative_to(output)))
    with zipfile.ZipFile(archive) as z:
        if z.testzip(): raise ValueError("Package ZIP CRC failed")
    report = {"schema_version":1,"artifact":str(output),"zip":str(archive),
              "zip_bytes":archive.stat().st_size,"zip_sha256":hashlib.sha256(archive.read_bytes()).hexdigest(),
              "manifest_files":len(manifest),"game_package_sha256":build["modules"]["gold_miner"]["package_sha256"],
              "validation":json.loads((output/"validation.json").read_text()),
              "camera_adapter_bound":False,"executed_on_camera":False,"flash_ready":False}
    report_path = ROOT/"research/generated/custom-apps-A01.json"
    report_path.parent.mkdir(parents=True,exist_ok=True)
    report_path.write_text(json.dumps(report,indent=2)+"\n",encoding="utf8")
    print(json.dumps({k:report[k] for k in ("zip","zip_bytes","zip_sha256","manifest_files","executed_on_camera")},indent=2))


if __name__ == "__main__":
    main()
