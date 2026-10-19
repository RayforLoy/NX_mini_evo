# NX_mini_evo

三星 **Samsung NX mini** 的固件逆向、可复现补丁与配套工具研究，基于官方机身固件 **1.10**。

项目包含离线固件工具、无触点镜头拍照补丁、自拍闪光菜单研究、无线传图原型，以及 ARM 自定义软件实验。

[下载预发布版本](https://github.com/RayforLoy/NX_mini_evo/releases) · [C06 修改与验证](docs/candidate-C06.md) · [实机测试与恢复](docs/hardware-validation.md) · [研究报告](docs/research-report.md)

## 当前版本与验证状态

**最新候选：C06，实验预发布。** C06 保留 C05 的 7 处修改，新增 6 处修改，补齐翻屏自拍时的 MENU 闪光项目、Fn 第四行和 Fn 闪光选项。

| 范围 | 当前证据 | 尚待验证 |
| --- | --- | --- |
| C05 无镜头拍照 | 用户已报告实机有效 | 完整无镜头测试矩阵、原厂镜头回归 |
| C06 自拍闪光 | 离线执行 MENU／Fn、设置存储和原厂 AE 路径；13 处补丁一致 | 机身屏幕、闪光充电、预闪、曝光同步及成片 |
| 固件容器 | 7 段解压、163 个资源、已知校验通过；官方无修改重组逐字节一致 | 修改后整机运行及恢复 |
| 自动曝光 | C05／C06 单档光圈与 Smart Auto 补偿离线验证 | 传感器测光与实拍曝光 |
| 传图与手机 | PC AutoBackup 接收原型、分页原图 API、群晖配置、Android 构建 | 相机配对、DSM、Z Fold7／Quick Share 联调 |
| 自定义软件 A01 | ARM 加载器、运行时、原创黄金矿工通过仿真，可在电脑预览 | 相机入口、屏幕和按键接口 |

截至 **2026-10-19**，用户已报告官方机身 1.10、9mm 镜头 1.11 和 C05 刷写完成；C06 仅完成写卡及读回校验，机身安装和功能验证尚无结果。以上实机状态来自用户反馈，未读取机身内部固件。

**C06 不是已验证的稳定固件。** `flash_ready`、`hardware_verified`、`recovery_verified` 均为 `false`。电脑端备份和离线测试不能替代实机恢复验证；开始机身测试前请阅读[恢复前置条件](docs/hardware-validation.md)。详细历史见[阶段记录](docs/status-20261019.md)和[机器验证记录](research/validation.json)。

## 下载与文件校验

在 [Releases](https://github.com/RayforLoy/NX_mini_evo/releases) 获取 C06 实验候选包及 `.sha256` 文件。候选包包含 BIN、补丁清单、复现工具和研究记录；Git 源码仓库不包含官方固件、提取资源或本地工具链。A01 是独立软件实验包，不是可刷游戏固件。

下载 ZIP 后，在 PowerShell 中计算 SHA-256，并与同名 `.sha256` 文件比较：

```powershell
Get-FileHash .\nxmini-evo-C06-release.zip -Algorithm SHA256
Get-Content .\nxmini-evo-C06-release.zip.sha256
```

C06 BIN：`manual-lens-C06_OFFLINE_ONLY.bin`，135,010,548 字节，SHA-256：

```text
d0780b10ecfec245e3ed77614b6e9779f915c2d2d25b7bb1bcab5cd2e3b7a1cd
```

## 快速开始

需要 **Python 3.11+** 和 Git。以下示例使用 Windows PowerShell、Python 3.12；命令仅操作电脑上的文件，不执行机身刷写。

```powershell
git clone https://github.com/RayforLoy/NX_mini_evo.git NX_mini_evo
Set-Location NX_mini_evo
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test,analysis,images]'
.\.venv\Scripts\python.exe -m nxmini --help
.\.venv\Scripts\python.exe -m nxmini fetch
.\.venv\Scripts\python.exe -m nxmini verify data/firmware/DATANXmini.bin --deep
.\.venv\Scripts\python.exe -m pytest -q
```

`fetch` 下载并校验哈希固定的官方 1.10 原件。官方 BIN 的 SHA-256：

```text
8a5bbe8885df239739f85f92087fea3972c05f98bd6521d809ed60dd5aca89fa
```

### 提取、分析与无修改重组

```powershell
.\.venv\Scripts\python.exe -m nxmini extract data/firmware/DATANXmini.bin work/nxmini-1.10
.\.venv\Scripts\python.exe -m nxmini analyze data/firmware/DATANXmini.bin work/analysis
.\.venv\Scripts\python.exe -m nxmini repack work/nxmini-1.10 work/roundtrip.bin
.\.venv\Scripts\python.exe -c "from pathlib import Path; assert Path('data/firmware/DATANXmini.bin').read_bytes() == Path('work/roundtrip.bin').read_bytes(); print('identical')"
```

提取目录及二进制输出拒绝覆盖；重复运行时请选择新的输出路径。

### 从官方原件重建 C06

```powershell
.\.venv\Scripts\python.exe -m nxmini patch data/firmware/DATANXmini.bin patches/manual-lens-c06.json work/C06-rebuilt.bin
.\.venv\Scripts\python.exe tools/verify_candidate.py --candidate work/C06-rebuilt.bin --manifest patches/manual-lens-c06.json --output work/C06-rebuilt-verification.json
```

本次发布全量复核为 **197 项测试通过，0 失败／错误／跳过**，见[发布测试记录](research/generated/release-c06-tests-20261019.json)；此前 C06 的 173 项验收、独立整包验证及二次重建记录仍保留。覆盖范围和模型边界见 [C06 文档](docs/candidate-C06.md)。

## 项目导航

| 目录／文档 | 内容 |
| --- | --- |
| `nxmini/`、`tests/` | Python CLI、固件解析与补丁、探针、接收端及测试 |
| `patches/`、`experiments/` | 哈希绑定补丁清单、汇编与独立实验 |
| `firmware/apps/` | ARM 软件包接口、加载器、运行时及原创游戏 |
| `android/`、`deploy/` | Android 接收端和群晖容器配置 |
| `research/`、`profiles/` | 验证证据、功能状态与原创色彩参数 |
| `tools/`、`docs/` | 构建、打包、复现脚本与研究文档 |

- [固件格式与补丁接口](docs/firmware-format.md) · [C05](docs/candidate-C05.md) · [C06](docs/candidate-C06.md)
- [自定义软件、ARM 编译与黄金矿工 A01](docs/custom-apps.md)
- [传图、群晖、Android 与 Quick Share](docs/network-and-android.md)
- [色彩参考与 CUBE 导出](docs/styles.md) · [NX-M 被动抓取研究](docs/nx-m-protocol.md)
- [功能状态](research/features.json) · [后续计划](docs/backlog.md) · [来源与素材许可](docs/third-party-notices.md)

四种色彩风格目前为电脑端原创参考，尚未完成品牌标定或机内 JPEG 接入；NX-M 引脚、电平和命令仍待实测。厂商固件、反编译代码、第三方工具链及维修手册不随源码分发，详见来源记录。
