# 官方 1.10 升级准备与机身确认

**当前卡文件提示：** 2026-10-19，C05 已由用户刷入；后续准备的卡根目录 `F:\DATANXmini.bin` 现为 **C06 实验候选**，尚待机身更新。下文官方写卡步骤是历史记录；官方原件另存于 `F:\NXMINI_OFFICIAL_110_20261019_170045.BAK` 和项目 `data/firmware/DATANXmini.bin`，C05 另有 `.BAK` 备份。本次候选写卡见 [C06 说明](candidate-C06.md)，不要仅凭根目录文件名将其当作官方原件。

2026-10-19，用户确认机身当前是 **1.04**，并授权先升级到最新官方固件。三星 NX mini 支持页面目前列出的固件为 **01.10（2015-01-30）**。

电脑端准备完成后，用户已于同日报告完成官方机身更新，当前机身按 **01.10** 记录；该确认来自用户报告，电脑没有读取机身内部版本。随后用户授权继续更新原厂 9mm 镜头。

## 已完成的准备

- 确认 USB 设备 `04E8:1404`，服务为 `USBSTOR`；当前 `F:` 对应 Samsung Digital Camera 的存储卡，`E:` 为 i-Launcher 只读光盘。
- 保存 44 个小型支持／配置文件，共 519,482 字节，并核对备份哈希。该备份来自卡和虚拟光盘，不包括机身内部固件、启动区或校准区。
- 重新验证官方 ZIP、BIN 的固定哈希，独立解析 7 段代码、163 个资源及已知校验。
- 将官方原件写入临时文件，flush／fsync 后完整读取校验，再改名为 `F:\DATANXmini.bin`，再次完整读取校验通过。
- 卡内照片未删除，存储卡未格式化。写入的是官方原件。

```text
文件：F:\DATANXmini.bin
字节数：135010548
SHA-256：8a5bbe8885df239739f85f92087fea3972c05f98bd6521d809ed60dd5aca89fa
```

用户已报告电池满格。三星要求满电后执行机身更新，且更新会重置拍摄设置；升级期间保持供电，不要取下电池或主动关机。

## 在机身完成更新

1. 在 Windows 安全弹出 Samsung Digital Camera，关闭相机，拔下 USB，再打开相机。
2. 进入 `MENU → 设置（齿轮）→ 设备信息 → 固件更新（Firmware Update）→ 机身固件（Body Firmware）`。用户手册也将更新入口写作 Software Update。
3. 确认屏幕显示当前 `01.04`、目标 `01.10`，选择“是”。
4. 等待进度完成和相机自动关机，再重新开机。
5. 回到设备信息，确认机身版本为 `01.10`，并记录结果。重新拍摄的一张 JPEG 的 Software EXIF 也可作为补充证据；旧照片中的 01.04 不会因机身升级而改变。

电脑目前只能看到存储卡，不能远程按机身菜单。复制成功不代表机身已更新。原始文件准备报告保留当时的未完成状态；后续用户完成报告记录于 `research/device-profile.json`，其中机身更新为 `completed: true`、来源为用户报告。

## 原厂 9mm 镜头 1.11 更新

用户报告原厂 NX-M 9mm F3.5 镜头版本为 1.00，并授权升级。三星该镜头支持页列出 **01.11（2015-04-09）**。本次重新从三星官方下载 ZIP，与此前固定哈希逐字节一致；ZIP 的 CRC 校验通过，唯一成员为 `lens.bin`。

- 官方 ZIP：`9mm_FW_v01.11.zip`，29,331 字节，SHA-256 `69115867825d9059b86ee84a992f92f318ff734fa67f3a476911ef5873e6e74b`。
- 卡上文件：`F:\lens.bin`，171,136 字节，SHA-256 `c5ecc497b348cc1a89441c3377f115b59384c66d1bb6ed30b6f39885e4ea0b8e`。
- 写入前重新确认 F: 对应 USB Samsung Digital Camera，非系统／启动磁盘；临时文件独占创建，flush／fsync，完整读回后改名，再次完整读回一致。
- 用户随后报告已完成原厂 9mm 镜头 1.11 更新。完成状态及用户报告来源写入 `research/device-profile.json`；原始文件准备报告保留写卡时的未完成状态，不覆盖历史证据。

保持 9mm 镜头安装、电量充足。在 Windows 安全弹出相机，关机并拔下 USB 后重新开机，进入 `MENU → 设置 → 设备信息 → 软件更新／固件更新 → 镜头固件（Lens Firmware）`。确认当前 `01.00`、目标 `01.11` 后选择“是”。更新期间不要关机、移除电池或拆下镜头。等待完成提示，再重新开机并检查镜头版本为 `01.11`。

已核对[三星官方镜头支持页](https://www.samsung.com/uk/support/model/EX-YN9ZZZZASEP/)和[官方镜头升级指南](https://downloadcenter.samsung.com/content/FM/201404/20140417090945506/NX_Lens_FW_upgrade_guide_i-Launcher_ENG.pdf)；后者第 4、5 页已渲染检查，明确支持直接下载解压后复制到 SD 卡，以及镜头固件菜单和完成提示。

本次写卡记录：[官方镜头 1.11 准备报告](../research/generated/official-lens111-preparation-20261019.json)。

## 依据与记录

- [三星 NX mini／9mm 官方支持页面](https://www.samsung.com/uk/support/model.EV-NXF1ZZB1IGB/)
- [三星官方升级指南](https://downloadcenter.samsung.com/content/FM/201405/20140502144131955/NXmini_FW_upgrade_guide_i-Launcher_ENG.pdf)，PDF 第 2、5、6 页；机身菜单和完成后自动关机已核对截图。
- [三星官方用户手册](https://downloadcenter.samsung.com/content/UM/201503/20150320185335787/NX_mini_English.pdf)，印刷页 155、177。
- [本次文件准备记录](../research/generated/official110-preparation-20261019.json)
- [设备记录](../research/device-profile.json)

后续用户已报告成功刷入 C05、无镜头拍照有效；自拍闪光仍失败。完整成像矩阵及恢复未验证，C06 也尚未完成机身更新和实测，当前结果见 [C06 说明](candidate-C06.md)。
