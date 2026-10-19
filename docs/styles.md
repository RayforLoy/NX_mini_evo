# 风格接口与机内直出研究

当前四个配置是原创的 sRGB 风格近似，用来验证工具与建立离线对照。没有复制品牌 LUT，也没有经色卡匹配证明能复现某一品牌机型。名称明确使用“风格参考”，机内 JPEG 直出尚未实现。

| 配置 | 参考方向 | 当前输入／输出 |
| --- | --- | --- |
| `profiles/fuji-muted-chrome.json` | 富士方向的低饱和、柔和高光 | sRGB → sRGB |
| `profiles/ricoh-positive.json` | 理光正片方向的对比与暖高光／冷阴影 | sRGB → sRGB |
| `profiles/leica-monochrome.json` | 徕卡方向的黑白层次 | sRGB → sRGB |
| `profiles/hasselblad-natural.json` | 哈苏方向的温和自然色彩 | sRGB → sRGB |

## 接口与使用

配置声明 schema、ID、来源、输入／输出色彩空间、矩阵域、3×3 矩阵、饱和度、阴影／高光偏色和单调曲线。当前运算顺序：sRGB 解码到线性 RGB → 矩阵 → 饱和度 → 偏色 → 限幅 → sRGB 编码 → 单调曲线。每次渲染记录限制到色域边界的通道样本数。

```powershell
.\.venv\Scripts\python.exe -m nxmini style sample.jpg profiles/ricoh-positive.json work/ricoh-reference.jpg
.\.venv\Scripts\python.exe -m nxmini style-export profiles/ricoh-positive.json work/ricoh-reference.cube --size 17
```

输入仅 JPEG／PNG，源文件和输出必须不同；ICC 输入先转换为 sRGB，无 ICC 的 Adobe RGB 被拒绝。输出保留校正后的 EXIF 和 sRGB 标识。RAW 不作为渲染输入，也不会修改 SRW。导出 CUBE 的 R 维变化最快，B 维最慢；仅供匹配的 sRGB 流程使用。

当前配置不支持 V-Log 输入；不是把 `input_space` 字段改名就可以转换其他空间。需要明确传递函数、原色、白点与输入设备响应，使用已验证转换或成对采样拟合。

## 参考项目与移植边界

[Sony PMCA Ricoh mod](https://github.com/bonyback1/sony-pmca-ricoh-mod) 展示了在 Sony 已有图像接口上使用 WB、曲线和矩阵的方法。其 Android／Sony 硬件接口不能直接运行于 NX mini REALOS。后续 NX mini 移植应先确定原厂参数通路与默认状态恢复，再评估自定义 LUT。

[V-Log-Alchemy](https://github.com/shenmintao/V-Log-Alchemy) 的原始创意 LUT 设计面向 Panasonic V-Log／V-Gamut，另有指定机型的输入适配。NX mini JPEG 和 ISP 输入都不是这些已适配的机型；任何引入必须逐项记录素材许可、输入／输出空间与转换来源。[上游说明](https://github.com/shenmintao/V-Log-Alchemy/blob/e51ba9a23458ff5c316f630b860eb0201139d126/README.md)

相关项目根许可证均标记 Apache-2.0，素材仍需逐项核对来源与附加说明。当前没有引入它们的 LUT、数值矩阵、图片或 APK，四个配置的参数均为本项目原创近似。

## 标定与验收

以原厂 9mm、固定曝光／ISO／白平衡，拍摄色卡、灰阶、肤色、饱和色和高光，覆盖日光、钨丝灯及常用 LED。保留 SRW+JPEG，记录机内画质参数与拍摄环境。若要标为某个品牌机型的模拟，还需同场景目标机成对采样。

先校准 NX mini 默认输出，再拟合曲线／矩阵；将用于拟合与验证的样本分开。记录色差、灰阶单调性、溢出比例和主观肤色结果。机内验收另外检查开关滤镜的状态恢复、JPEG 尺寸／EXIF、连拍速度、内存、RAW 原始像素数据完整性。不同照片的 SRW 文件哈希不应被当作 RAW 像素相等测试，因为时间戳和元数据本来会变化。

只有识别 `IQ_*` 等资源的实际结构、调用点、量化和更新时机后，才尝试机内参数实现。目前没有向固件资源注入参考矩阵或生成可刷滤镜包。
