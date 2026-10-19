# NX mini evo 研究报告

本页保留 2026-10-17 至 2026-10-18 前期研究阶段的结论。基线为三星官方 1.10，所有偏移与函数定位均绑定该文件 SHA-256。后续已形成含 7 处代码修改的 [C05 离线候选及验证包](candidate-C05.md)，尚未实机刷写；功能现状以该文和设备记录为准。2026-10-19，用户报告官方机身 1.10 与原厂 9mm 镜头 1.11 更新完成。

## 参考项目与实际采用的方法

| 项目 | 可借鉴的方法 | 本项目的落地 |
| --- | --- | --- |
| [Magic Lantern](https://github.com/reticulatedpines/magiclantern_simplified) | 借原厂运行环境扩展功能、按机型定位入口 | 寻找可撤销入口；尚未发现 NX mini 的已验证存储卡加载方法 |
| [CHDK](https://github.com/pelrun/CHDK) | 函数签名、平台适配和机型／版本绑定 | 每项发现记录固件哈希、指令模式、原字节及证据；该链接是过时归档镜像，仅作历史方法参考 |
| [OpenMemories-Tweak](https://github.com/ma1co/OpenMemories-Tweak) | 调用已有接口、修改已理解的设置 | 区分“已有功能解锁”和“新增处理”；NX mini 不能直接安装 Sony PMCA 应用 |
| [Nikon firmware tools](https://github.com/simeonpilgrim/nikon-firmware-tools) | 解码、反汇编、模拟与补丁工具 | 独立实现容器／RELC、固定基线与字节前像检查、修改后解码验证 |
| [Samsung NX Hacks](https://github.com/ge0rg/samsung-nx-hacks) | M7MU 容器、压缩与网络研究 | 直接参考公开格式；NX mini 的 REALOS 分析与 Linux 型 NX 严格分开 |
| [PC AutoBackup Python 3](https://github.com/warwickh/pc-autobackup) | SSDP、UPnP／SOAP 与相机推送 | 独立实现标准库接收原型；上游已测试名单未列 NX mini，不能据此声称适配完成 |
| [Samsung NX emailservice](https://github.com/ge0rg/samsung-nx-emailservice) | 替代相机已停用的分享服务 | 上游明确列出 NX mini 可用，可作为备用；本地尚未部署或验证尺寸／元数据 |
| [FlashAir 官方 Lua 示例](https://github.com/FlashAirDevelopers/LuaTutorial) | 在存储卡侧做文件访问和网络传输 | 借鉴异步文件传输、重试与索引设计；不能把全尺寸 Wi-Fi SD 方案视为 microSD 兼容硬件 |

参考提交固定在 `research/sources.json`。没有将上述项目的源代码或厂商 LUT 复制进本仓库。

## 官方固件与工具结果

下载来源为[三星官方 NX mini 1.10 ZIP](https://downloadcenter.samsung.com/content/FM/201501/20150130171537129/NXmini_FW_v01.10.zip)。文件尺寸与校验值见格式文档；7 段解压共 21,435,384 字节，163 项资源完整列出。解析发现 `REALOS/ARM`、ITRON 与 PrCONNECT 系列标记，应按 RTOS 分析，不能由某个 `linux` 文件名推断整个机身运行 Linux。

2026-10-19 复核：解压段 1 的 `0x33b518`、`0xa2b338` 含 `SOFTUNE REALOS/ARM` 及 Fujitsu Semiconductor 版权标识，`0x2a5990` 含 `REAL OS/ITRON4`；段 2 含 PrCONNECT Pro 网络组件路径。具体上下文与解压段哈希见 [系统静态识别证据](../research/generated/os-platform-evidence.json)。这些证据支持 REALOS/ARM 实时系统判断，未证明内核确切版本、完整系统调用接口或动态模块加载机制；版权年份不能作为机身固件构建年份。

自定义程序可研究为两层：先定位可用代码／RAM 空间、原厂调用约定、任务与文件／显示／网络接口，在固件内挂接小型 ARM 扩展；再研究从卡读取并校验版本／大小／哈希后加载模块的机制。脚本解释器需要在该执行入口及资源预算成立后另行移植。当前 C05 只修改已有代码路径并复用原厂函数，没有新增通用加载器，也未在机身运行自编任务；`nolens.txt` 是已定位的工厂文件开关，不是任意程序执行入口。

资源中存在 `IQ_CAP.BIN`、`IQ_COMM.BIN`、多组 `IQ_M_* / IQ_V_*`、`CAPTURE.BIN`、`COMMON.BIN`、`res.dat`、`PRD_CMD.XML`、Wi-Fi 固件和 MCU 资源。这些名字用于确定分析方向，并未证明任一字节就是可直接替换的色彩 LUT。

离线工具已经实现两项加法校验和固定布局重组。无修改重组与原件逐字节一致；测试中的官方文件单字节修改只验证编码与校验流水线，没有任何功能含义，也没有保存为可刷版本。

## Ghidra 可复现分析

```powershell
.\.venv\Scripts\python.exe tools/bootstrap_analysis.py
powershell -ExecutionPolicy Bypass -File tools/run_ghidra.ps1
```

本地固定 Ghidra 12.1.4 和 Temurin JDK 21.0.12.1+1，下载哈希及位置保存在 `tools/local/versions.json`。段 1 作为 ARM 小端二进制加载到 `0x40000000`；包含 ARM 与 Thumb 混合代码。脚本分别设置已知入口的模式，并修正自动分析误判的虚函数 UI 区域。原始固件字节不会被这些脚本修改。

首轮自动分析设 300 秒上限，实际超时后保存了部分结果。自动识别的约 18,000 个函数包含误判，不能当作完整程序地图。第二轮补充了虚表到达的 UI 入口；当前导出约 90 个选定函数用于复核。`analysis_complete` 始终为 false。导出目录在 `work/ghidra-export`，反编译结果只保存在本地。

完整字符串索引由 `analyze` 生成。`research/anchors.json` 是人工复核后的少量锚点，函数地址依赖上述静态映射，未通过运行时读内存确认。

## 手动镜头与智能自动档

已定位到两个不同层面的路径：

1. `0x4022e200` 的工厂启动流程检查 `I:/nolens.txt`、`nolens_card.txt` 及 J 盘同名文件，设置生产状态和模式标志，还涉及校准／调整入口。这只能确认生产流程存在这些文件开关，不能把创建文件等同于普通拍摄模式的无镜头解锁。
2. `0x4016009c` 对应光圈数量相关 UI 逻辑，格式串含 `CUiModeASM::getApertureCount`、`isLensDetached`、`isKMountAttached`。虚表槽 `0x458 / 0x45c` 的返回值影响光圈计数。它还不是已证明的快门许可判断。

进一步追踪确认，`0x403f4090` 是另一条 `nolens on/off` 调试命令处理路径，切换共享状态 `0x419f2424+0xc` 的 bit 30，与工厂文件入口的标志不同。候选应用虚表 `0x40773cdc` 中，槽 `0x458` 指向 `0x403f0e98`，读取共享状态 `+0x28` 的 bit 4；槽 `0x4b8` 指向 `0x406d4324`，在镜头类型查询为 1 或调试 nolens 位为 1 时返回真。

镜头类型查询 `0x40448440` 比较返回数据的 `XM / XL` 前缀，缓存 1／2；这些前缀的物理含义仍需实机确认。已找到消费者 `0x40347fe8 / 0x40348e9c`，还依赖设置选择器 9／10／0x18 与模式条件。生产命令式拍摄函数 `0x4017e7e4` 也设置同一 nolens 位。上述调用关系提供了继续分析的具体入口，但还不能把该位解释为普通拍摄的完整解锁，更不能直接证明 Smart Auto 的 AE 行为。

可对已有 Ghidra 项目运行 `tools/focus_ghidra.ps1`，导出这些函数及直接调用图到 `work/ghidra-lens-focus`。脚本显式修复已确认的 ARM 函数边界；当前定向导出 21 个函数，没有未解析的指定入口。调用图仍受静态映射和间接调用识别能力限制。

下一步需从正常半按／全按快门事件追踪镜头状态消费者，分离“镜头未连”“原厂 9mm”“电子转接环”和“独立手动镜头”。智能自动档还要单独追踪 AE 分支，确认未知光圈信息不会导致拒拍或错误曝光；实测固定亮度、手动变光圈时快门／ISO 的响应。当前未生成跳过检查的盲目分支补丁。

## 自拍与内置闪光

`0x4055c8d8` 的 ARM UI 入口会修改自拍相关状态、共享字节 `+0x74` 以及 `0x419f077c+0x8d0` 的 UI 标志；`0x405021b8` 的父模式转换包含 `0x2006` 分支。这为继续查找模式能力表提供入口，但尚未证明哪一条条件直接禁止内闪。

另一个 `0x401ff110` 函数的 `FlashEnable=0` 日志位于自动对焦算法中；单凭该字符串不能据此修改闪光逻辑。

[三星手册](https://downloadcenter.samsung.com/content/UM/201503/20150320185335787/NX_mini_English.pdf)记录了翻屏自拍的倒计时、美颜和触控人脸拍摄行为，并明确记录转接环场景的闪光限制。手册没有证明所有自拍禁闪都由同一个开关造成。实机须分别改变翻屏、自拍模式、美颜、倒计时及转接环，首轮只测内置闪光。

## 隐藏项与实验菜单

`PRD_CMD.XML` 含 `<8step_drive>` 等不符合标准 XML 名称规则的条目，因此采用保留原行的专用解析，提取 237 个叶子命令。清单见 `research/generated/factory-catalog.json`；每项包含命令／索引号、标签、来源、验证状态和 `executable: false`。

这些命令不是 237 个已经能打开的普通菜单选项，也不代表全部隐藏项已穷尽。当前交付的是主机端清单，机内实验菜单未植入。生产测试、调整和闪存类入口必须进一步识别读取范围、原值存储位置和恢复路径，完成后才能开放写入。没有用猜测的“备份”函数包裹未知写入操作。

## 网络、色彩和协议结论

传图现阶段采用相机推送到接收端、手机从接收端取图的原型路径。接收端保存收到的原始字节，软件回放测试覆盖去重和失败重传；相机是否推送原尺寸 JPEG、SRW 和视频仍未知。MobileLink／AutoShare 直连、机内 FTP／HTTP 和机内 Quick Share 都未实现，详细边界见网络文档。

四个风格参考是原创 sRGB 参数，便于建立离线对照，尚未基于 NX mini 色卡标定。Sony ISP 和 Panasonic V-Log 项目提供方法参考，不能直接装到 NX mini；机内还需要图像处理接口、曲线／矩阵格式和恢复默认值路径。

NX-M 文档目前是有证据等级的草案。没有把普通 NX 卡口的引脚编号、电平或通信命令套用过来。原厂 9mm 与逻辑分析仪／引出结构到位后，先被动抓取启动、光圈、AF 和休眠，再形成可用于电子转接环的命令表。
