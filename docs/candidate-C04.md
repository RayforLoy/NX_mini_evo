# C04：手动镜头路径与自拍闪光菜单候选

**这是离线候选，尚不可刷写。** C04 保留 C03 的 5 处修改，新增 1 处自拍闪光菜单修改。已改变固件本体；机身拍摄、充电、预闪、曝光同步及恢复仍未验证，不能将菜单解除限制等同于完整自拍闪光修复。

固件：`dist/nxmini-evo-C04/manual-lens-C04_OFFLINE_ONLY.bin`，135,010,548 字节。

```text
官方输入 SHA-256
8a5bbe8885df239739f85f92087fea3972c05f98bd6521d809ed60dd5aca89fa

C04 输出 SHA-256
a8e71a56bc55bfa9706a977457429945ae9125e05422770caeb75cb8fa957fa8
```

## 本次实际修改

`0x40354a94` 对闪光选择器 **十进制 10** 调用 `0x40354560`，进入专用界面。构造函数 `0x403ff100` 通过字面量 `0x403ff620` 写入虚表 `0x4076a87c`，RTTI 名称为 `10CMenuFlash`。该界面的初始化 `0x403ff318` 调用选项生成函数 `0x403ff24c`。

原厂选项生成函数没有自拍 `0x2006` 的独立分支，因此它落入仅生成值 0 的默认路径。原厂 Program 等模式则生成 `[0, 4, 5, 6, 7]`。此前只检查设置 getter 和参数缓存，遗漏了这个界面分支。

| 项目 | 内容 |
| --- | --- |
| 修改位置 | 代码段 1 偏移 `0x3ff288`，分析地址 `0x403ff288` |
| 原字节／指令 | `03 00 00 ea`，`b 0x403ff29c` |
| 新字节／指令 | `05 10 60 e2`，`rsb r1, r0, #5` |
| 自拍选项 | `[0]` → `[0, 4, 5, 6, 7]`，采用原厂 Program 的值和元数据 |

该位置仅在较低模式编号的未处理分支执行，此时 `r0 = mode - 0x2002`。新指令计算 `r1 = 5 - r0`，随后进入原有比较：自拍得到 `r1=1`，转入原厂完整选项路径。其他模式的选项列表不变，已执行所有 65,536 个 16 位模式编号的前后对照。

没有移动函数、加入代码洞或删除后续有效性判断。`0x403ff11c` 的原厂选项帮助函数仍调用 CMenuLib 的可用性及元数据函数。驱动值 3、动态范围值 2、镜头资格不通过时，原始过滤和缓存清零仍有效。

## 验证边界

验证器独立解压整个候选，逐段比较清单中的改动，并核对 163 个资源、writer、其他区域及已知校验。同时执行 C03 的原厂 S2／准备／默认光圈表验证，以及新增的：

- 全部 16 位模式的原始选项生成指令；只有自拍选项列表改变。此遍历以原厂 `0x403ff11c` 的调用参数为边界，将该帮助函数替代为记录器。
- 8 个代表模式继续执行原厂选项帮助函数、可用性和元数据读取；最终 UI 行接收函数、内存复制、设置输入、镜头类别、日志及共享 UI 计数为显式模型。
- 自拍新增各值分别送入原厂参数 setter，确认非零参数仍可进入 RAM 缓存；另验证上述 3 个原厂限制仍拒绝。

上述初版缓存测试使用明确指定的设置输入。后续 r2 验证包进一步执行了下节的原厂菜单行与设置读写链。两者都没有绘制真实界面、驱动按键／触摸事件或执行非易失存储持久化，不能当作完整实机菜单操作、闪光硬件或成片证明。详细边界及执行记录在 `research/generated/manual-lens-c04.json`。

段 1 压缩长度为 5,301,884 字节，比官方多 16 字节，仍在原分配空间内。其他段位置和整包长度不变。

首包完整回归 151 项通过；r2 更新后为 **153 项通过，0 失败、0 错误、0 跳过**，包含固定输出哈希重建、原指令菜单对照和选中行提交验证。测试记录见 `research/validation.json`。

C03 的默认单档光圈值仍是内部默认量，不能当作手动镜头真实光圈。完整 AE／Smart Auto 实际测光及原厂 9mm 时序回归继续按 [C03 说明](candidate-C03.md) 验证。

## r2：原厂菜单行到设置 RAM 的执行验证

固件二进制保持上述 C04 哈希，r2 更新验证代码和记录。原厂 `0x403ff318` 初始化、有效设置读取、选项生成及 `0x403fe49c` 行存储实际执行，取代此前最终 UI 行接收函数的模型。

随后以选中行编号为明确的模型输入，执行原厂 `0x403ff4b4 → 0x403ff434`。它从行结构读取值，经 CMenuLib `+0x1c → 0x40346dd0`、`0x403570b0` 写入设置 RAM，再经 CMenuLib `+4 → 0x40345eb0` 调用应用层闪光 setter，执行到 `0x406c3b98` 的两处参数缓存边界。设置字节 getter `0x40356da8` 同样执行原始指令，未用字典替代返回值。

15 组比较覆盖原厂 Program、C04 Program、C04 自拍，以及值 `0,4,5,6,7`。自拍只改变索引 `0x161`，Program 只改变 `0x15f`；另一个模式预置值 7 保持不变。重新执行界面初始化会从 RAM 读回该值并选中对应行。清洁 RAM 表中选择原值不产生字节写入或脏标记；3 个原厂闪光限制在这条路径中仍过滤选项。

这里的设置表是按原厂布局初始化的模型 RAM，**不是实机 EEPROM 或闪存备份**。对象分配、初始设置值、当前模式、选中行、无活动弹窗、共享 UI 计数、内存复制、互斥锁、时钟／日志、镜头类别和相机状态仍有显式模型。原厂按键／触摸派发、断电保持、充电、预闪及成片均未执行。记录的 `selected_row_to_ram_setting_commit_executed=true` 与 `settings_nonvolatile_persistence_executed=false` 需一起阅读。

r2 工具包为 `dist/nxmini-evo-C04-offline-candidate-r2.zip`，其中的固件二进制与首包相同。

## 离线复现

将工具包解压到新目录，进入该目录。首次配置：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[test,analysis,images]'
.\.venv\Scripts\python.exe -m nxmini fetch
.\.venv\Scripts\python.exe -m nxmini extract data/firmware/DATANXmini.bin work/nxmini-1.10-verified
```

然后执行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m nxmini patch data/firmware/DATANXmini.bin patches/manual-lens-c04.json work/rebuilt-C04.bin
.\.venv\Scripts\python.exe tools/verify_candidate.py --candidate work/rebuilt-C04.bin --manifest patches/manual-lens-c04.json --output work/C04-verification.json
```

工具拒绝覆盖既有二进制输出；本工作区已有原件和提取目录，直接运行最后两条时选择尚不存在的输出路径即可。重建输出必须匹配上述固定哈希。

## 实机验证顺序

先按 [实机验证与恢复前置条件](hardware-validation.md) 确认备份范围和实际可行的恢复入口；持有官方文件不代表机身能够恢复。

满足条件后，先用原厂 9mm 验证 Program 正常拍摄，再进入自拍，检查闪光菜单能否选择、退出再进入的设置保持、充电状态和实际成片。分别对照美颜开／关、翻屏、普通／倒计时、明暗环境和电量状态；记录 JPEG／SRW、EXIF 及是否闪光。再做原厂镜头切换和 C03 手动镜头验收。即使菜单显示成功，若不闪光，仍需追踪后续覆盖或 AE／硬件判断。

目前不提供经过实机验证的刷写及恢复步骤，`flash_ready`、`hardware_verified` 和 `selfshot_flash_fixed` 均保持 false。撤销离线修改使用哈希正确的官方原件重新生成，不向未验证的机身写入。
