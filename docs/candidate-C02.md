# C02：扩展手动镜头放行候选及验证

针对官方 NX mini 1.10。C02 在 C01 的三处修改上，补上原始准备判断中的第四处无镜头拦截。**这是离线候选；未验证机身启动、无短接成像、Smart Auto 曝光或恢复，不能作为可刷发布版。自拍内闪仍未修复。**

固件：`dist/nxmini-evo-C02/manual-lens-C02_OFFLINE_ONLY.bin`，135,010,548 字节。

```text
输入 SHA-256
8a5bbe8885df239739f85f92087fea3972c05f98bd6521d809ed60dd5aca89fa
输出 SHA-256
96301d75c5cf3d7e1218d2897665b8530caeceb6f3be0033f691cd46003908fd
```

## 四处实际修改

清单：`patches/manual-lens-c02.json`，输入原件哈希和每处原字节均固定；不匹配即拒绝生成。以下地址为段 1 解压后加载到 `0x40000000` 的分析地址，实际运行映射仍待机身确认。

| 地址 | 修改 | 原因 |
| --- | --- | --- |
| `0x4014dcc8` | 条件分支 → `mov r0,r0` | 普通全按不再仅因镜头脱离而转入取消／镜头事件 2 |
| `0x4027be18` | 条件分支 → `mov r0,r0` | 准备判断在 `共享状态 +4 & 0x400000` 时，也不再单独因镜头脱离而取消；这是 C01 未覆盖的分支 |
| `0x406c7ee8` | `tst r0,#0x40000000` → `movs r0,#1` | 事件 2 采用原厂 `nolens on` 对应路径 |
| `0x406c7fd0` | 同上 | 事件 6 采用原厂同一路径，避免该处无镜头弹窗 |

两处分支后的独立 `0x464` 镜头故障查询仍执行；真实镜头脱离位保持不变，没有伪造镜头信息。`0x400000` 状态位的实际触发来源尚未完整确认，因此用其数值描述，未将它直接命名为某种实机 AF 状态。

## 已验证的范围

单独验证脚本逐段解压并比较清单：7 个代码段完全符合预期改动，163 个资源、writer、未知尾部字节及其他未授权区域均未变化。两种已知校验有效，文件长度及后续段位置保持原布局。段 1 压缩长度增加 12 字节，仍在原有 404 字节余量内。

原始 ARM 路径探针覆盖 6 个模式值、两种 `+4` 状态值（`0x80000 / 0x480000`）及镜头连接／脱离，共 24 组。未连接时，C02 执行原始准备判断 `0x4027bde0`、`startS2Mode (0x4014eb60)`、设置读取和倒计时选择 `0x4023cb88`，再到达各模式的原始 `+0xf0` 拍摄分发入口：

| 原始模式虚表 | `+0xf0` 入口 | 探针边界 |
| --- | --- | --- |
| SmartAuto `0x4077452c` | `0x4014f51c` | 原始 `doPhotoCapture` 入口 |
| Program `0x40770f10` | `0x4054db14` | Program 包装函数入口 |
| ASM `0x4076abd4` | `0x4016058c` | ASM 包装函数入口 |
| SelfShot `0x407728d4` | `0x4055cc30` | 自拍包装函数入口 |

`doPhotoCapture` 名称已按日志引用核对：指令 `0x4014f954` 读取文字指针池 `0x4014fa18`，其值为 `0x407395b6`，对应 `doPhotoCapture` 字符串。C01 文档记录的 `0x4014eb60` 是 `startS2Mode`；`0x4044463c` 是取消半按，不能将这些不同入口混称为物理拍摄 API。

连接镜头时，候选和原件的受控路径一致；部分连接路径在电子镜头查询处停止。测试还验证 C01 的已知状态缺口、真实脱离位保留及独立镜头故障拒绝。完整机身行为尚不能由这些局部检查证明。

## 模型输入与未完成项

探针从合成 RAM 开始：全按前 `+8=0xc`、`+0x330=1`，应用状态为 0；EEPROM 读取默认值均为 0。相机状态查询、计时状态／设置、时钟、日志、取消半按及部分 UI 清理是逐项记录的模型。探针在上述拍摄分发入口停止，没有执行其中的 ISP、AE、传感器或闪光硬件调用。倒计时设置为零的成功路径不能证明真实自拍倒计时已完整跑通。

尚待完成：半按状态来源、请求之后的拍摄流程、未知光圈下的测光、Smart Auto 实际曝光、自拍内闪、原厂镜头整机回归，以及备份／故障恢复验证。C02 不能被标为这些功能已在相机上修复。

## 复现与验证

在项目根目录执行。新解压的交付包须先提取官方固件，供 ARM 探针测试使用；已有经过验证的 `work/nxmini-1.10-verified` 目录时跳过提取命令。提取目录和生成文件均拒绝覆盖，镜像复现请使用新的输出路径。

首次使用且尚无虚拟环境时，先运行 `py -3.12 -m venv .venv`。已有项目虚拟环境时直接使用即可。

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[analysis,test,images]'
.\.venv\Scripts\python.exe -m nxmini fetch
.\.venv\Scripts\python.exe -m nxmini extract data/firmware/DATANXmini.bin work/nxmini-1.10-verified
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m nxmini patch data/firmware/DATANXmini.bin patches/manual-lens-c02.json work/C02-reproduced.bin
.\.venv\Scripts\python.exe tools/verify_candidate.py --candidate work/C02-reproduced.bin --manifest patches/manual-lens-c02.json --output work/C02-verification.json
```

当前完整记录：`research/generated/manual-lens-c02.json`。测试中另从官方原件重新生成 C02，并比对固定输出哈希；验证器不调用补丁写入器，而是读取现成镜像检查。

## 后续实机验收

须先明确实际备份范围、升级接受条件和故障恢复入口；目前不提供未经验证的刷写步骤。条件满足后，按以下顺序记录照片、EXIF、机身状态及日志：

1. 原厂 9mm：启动、半按、单拍、连拍、存片、回放、关机。
2. 无触点转接环且不短接：P／A／S／M 启动、半按及反复拍摄，确认存片后没有新增弹窗或卡死。
3. Smart Auto：固定手动光圈，在不同亮度下检查快门、ISO、直方图和成片曝光，不能以“能按快门”作为曝光验收。
4. 原厂／手动镜头切换、自拍进入退出、倒计时回归；内闪问题单独验证。

保留哈希正确的官方原件作为回退依据；保留原件不等于已经验证恢复能力。
