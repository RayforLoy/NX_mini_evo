# 研究来源与素材记录

完整提交号见 `research/sources.json`。本项目独立编写 Python／Java 工具与四个风格参数；没有复制上游源代码、厂商图像、品牌 LUT 或反编译代码到源码目录。

| 来源 | 用途 | 许可／处理 |
| --- | --- | --- |
| [Samsung 官方 1.10](https://downloadcenter.samsung.com/content/FM/201501/20150130171537129/NXmini_FW_v01.10.zip) | 本地分析原件 | 原固件不纳入源码；下载固定哈希 |
| [Samsung 手册](https://downloadcenter.samsung.com/content/UM/201503/20150320185335787/NX_mini_English.pdf) | 行为与限制对照 | 引用链接，未重发整本手册 |
| [Samsung NX Hacks](https://github.com/ge0rg/samsung-nx-hacks)／公开 M7MU 格式 | 容器和压缩研究 | 格式参考，独立实现，无上游源码随附 |
| [PC AutoBackup](https://github.com/warwickh/pc-autobackup) | 协议互通研究 | 根 LICENSE.md 为 GPL-2.0；独立标准库原型，无其代码随附 |
| [Sony PMCA Ricoh mod LICENSE](https://github.com/bonyback1/sony-pmca-ricoh-mod/blob/5f5c7ce9e1d4b02c48aba9310e1278e7716611ab/LICENSE) | 参考 ISP 曲线／矩阵工作流 | Apache-2.0；未复制源码、LUT、素材或数值参数 |
| [V-Log-Alchemy LICENSE](https://github.com/shenmintao/V-Log-Alchemy/blob/e51ba9a23458ff5c316f630b860eb0201139d126/LICENSE) | 参考色彩空间适配与标定 | Apache-2.0；输入空间与素材来源须逐项核对，当前未复制 LUT |
| [FlashAir LuaTutorial](https://github.com/FlashAirDevelopers/LuaTutorial) | 文件访问与网络传输设计参考 | BSD-2-Clause 示例，无源码随附 |
| Magic Lantern、CHDK、OpenMemories、Nikon 工具、emailservice | 研究方法和替代服务方向 | 仅链接和方法归纳，未导入其代码或二进制 |

Ghidra、Temurin、Google Android SDK 下载在 `tools/local`，使用各自随附许可，未打包到项目源码。Python 可选依赖通过 pip 安装，保留各自许可证。品牌名称表示研究／风格方向，不表示获得厂商认证或已完成品牌色彩复现。

自定义软件 A01 的 C 加载器、运行时和黄金矿工玩法为独立实现；游戏没有使用原版 Flash、原版源代码或商用游戏素材。Arm GNU Toolchain 14.3.rel1 仅按官方 ZIP 哈希下载到 `tools/local`，工具链二进制不随 A01 软件包分发。电脑预览依赖 Unicorn 与 Pillow，通过用户环境安装并使用各自许可。Arm AAPCS32 及缓存文档只用于接口研究，链接见 [A01 说明](custom-apps.md)。
