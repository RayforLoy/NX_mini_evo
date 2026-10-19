# C03：无镜头放行与默认固定光圈约束候选

**这是离线候选，不可刷写。** C03 保留 C02 的 4 处放行修改，另增加 1 处默认光圈表修改。实机无短接拍摄、Smart Auto 测光／曝光、原厂 9mm 回归和故障恢复仍未验证；自拍内闪尚未修复。

固件：`dist/nxmini-evo-C03/manual-lens-C03_OFFLINE_ONLY.bin`，135,010,548 字节。

```text
官方输入 SHA-256
8a5bbe8885df239739f85f92087fea3972c05f98bd6521d809ed60dd5aca89fa

C03 输出 SHA-256
dbbaf3fb9c5ebbb227b0dbc050797abe03a69b8f84001c9c114b3c532343387e
```

## 新增修改的依据

原厂 `CAeLensIF` 初始化及镜头脱离回调共用 `0x40520de8` 的默认信息。该函数将 `IrisDrive=0`、`LensReady=0`、`OpenAv=64`、`AVZoom=0`，同时生成 21 个光圈节点，内部值为 `64, 68, …, 144`。

这些默认信息经过原厂 `0x40316d98` 的镜头字段赋值，以及 `0x403160c8` 的光圈表构造，进入曝光程序线。该程序线存在沿光圈表选择其他节点的可能；对无电子光圈的手动镜头，需要把可用光圈约束为单档。

| 项目 | 位置／证据 |
| --- | --- |
| 类型 | `0x4078041c` 虚表的 RTTI 为 `CAeLensIF` |
| 初始化 | `0x40520fd4`，名称字符串 `[AE_Lens]Initialize` 位于 `0x40521224` |
| 脱离回调 | `0x40521d94`，名称字符串 `[AE_Lens]AeLensCallBack_Detach` 位于 `0x40521e98` |
| 共同默认函数 | `0x40520de8` |
| 新增修改 | 段 1 偏移 `0x520ee4`：`mov r1, #0x15` → `mov r1, #1` |
| 原字节 → 新字节 | `15 10 a0 e3` → `01 10 a0 e3` |
| 镜头表读取 | `0x40521f8c` |
| 实际镜头数据加载路径 | `0x40521998`，收到表后重新写入节点数和内容 |

C03 只把这张默认表的节点数改为 1，保留第一项内部值 64。**这个值是原厂默认模型的内部数值，不能当作读到了手动镜头的实际光圈。** 接收实际镜头数据后的表仍由原厂加载函数覆盖。

其余 4 处修改与 C02 相同：`0x4014dcc8`、`0x4027be18`、`0x406c7ee8`、`0x406c7fd0`。完整原字节、修改内容和撤销说明在 `patches/manual-lens-c03.json`。

## 已执行的离线验证

- 整包独立解压：7 段逐字节匹配补丁清单；163 个资源、writer 和其余未授权区域不变，已知校验有效。
- 段 1 压缩长度为 5,301,880 字节，比官方多 12 字节，仍在原分配空间内；没有移动其他段。
- 24 组 S2 路径：两种准备状态、6 个模式、连接／脱离输入；继续执行原始准备判断、`startS2Mode` 和倒计时选择，停止在模式拍摄分发入口。
- 执行原厂镜头初始化、字段读取、AE 表复制和 AV 补偿函数：原件默认表为 21 项，C03 为 `[64]`；模拟数据加载后两者都接受 4 项表，执行真实脱离回调后分别恢复 21 项／1 项。
- 原厂 AV 补偿函数在受控输入 `Tv=40, Av=96, Sv=80` 下，C03 返回 `Tv=72, Av=64, Sv=80`，保持 `Tv+Av` 不变。另覆盖多组提出的 Av 值。

模型只替代对象分配、两段 libc 内存填充、日志／状态接口及镜头表来源；不模拟镜头电气协议。镜头表对照数据 `[56, 64, 72, 80]` 是合成输入，**不是原厂 9mm 的抓取数据**。

AV 补偿是 Smart Auto 等曝光程序线使用的共同函数。本次没有执行完整 AE 求解、传感器测光或拍摄，不能由局部数值守恒推导实机曝光正确。还需检查电子镜头初始化到数据加载之间的时序、无触点冷启动与热切换、亮度／手动光圈变化，以及曝光上下限、RAW／JPEG 和 EXIF。

机器记录：`research/generated/manual-lens-c03.json`。完整测试结果见 `research/validation.json`。

本轮完整回归 **149 项通过，0 失败、0 错误、0 跳过**，包含 C03 固定输出哈希重建、默认／脱离循环、模拟镜头表加载和多组 AV 补偿测试。

## 在新目录复现

将交付包解压到一个新目录，进入该目录。首次运行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test,analysis,images]'
.\.venv\Scripts\python.exe -m nxmini fetch
.\.venv\Scripts\python.exe -m nxmini extract data/firmware/DATANXmini.bin work/nxmini-1.10-verified
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m nxmini patch data/firmware/DATANXmini.bin patches/manual-lens-c03.json work/rebuilt-C03.bin
.\.venv\Scripts\python.exe tools/verify_candidate.py --candidate work/rebuilt-C03.bin --manifest patches/manual-lens-c03.json --output work/C03-verification.json
```

二进制和提取目录拒绝覆盖已有路径。当前工作区已经有官方原件和提取目录，可直接运行后两条命令，但需给重建文件选一个尚不存在的路径。验证器同时核对官方输入哈希、C03 输出哈希、修改前字节、整包差异以及上述原指令执行结果。

## 实机验收与恢复

按 `docs/hardware-validation.md` 完成恢复前置条件后，先验证原厂 9mm 初始化、AF、光圈、拍摄和镜头切换，再验证无触点冷启动、Smart Auto 中的亮度／手动光圈变化、曝光极限、取下镜头和重新装回镜头。记录实际成片和完整 EXIF，不能仅凭按钮响应或快门声音判定通过。

在离线文件中撤销修改，应从哈希正确的官方原件重新生成所需候选，或使用官方原件作比较。持有原件并不等于机身已具备可用的恢复路径。目前未提供经过验证的刷写或机身恢复流程，`flash_ready` 和 `hardware_verified` 保持 false。
