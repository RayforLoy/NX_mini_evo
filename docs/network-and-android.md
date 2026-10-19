# 传图接收端、群晖与手机

当前可运行软件路径：PC AutoBackup 兼容接收原型 → 磁盘原始文件 → 带令牌的 HTTP API → Android 保存／系统分享。与 NX mini 的配对、推送及三星手机 Quick Share 尚未实测。

## Windows 本地运行

已安装项目后，先在回环地址试运行：

```powershell
.\.venv\Scripts\python.exe -m nxmini serve --advertise-ip 127.0.0.1 --output work/receiver/photos --state work/receiver/state
```

确认相机实际 IP 后，替换示例地址，在家庭局域网启动：

```powershell
.\.venv\Scripts\python.exe -m nxmini serve --host 0.0.0.0 --advertise-ip 192.168.1.10 --camera-ip 192.168.1.50 --output work/receiver/photos --state work/receiver/state --ssdp
```

相机和接收端需处于允许组播的同一网络。服务 TCP 52235；SSDP 使用 UDP 1900／239.255.255.250。程序不会自动更改 Windows 防火墙。LAN 启动必须给出相机 IP 白名单，可重复 `--camera-ip`。下载 API 单独验证随机令牌，启动输出仅显示令牌文件路径；不要把令牌提交到仓库。

PC AutoBackup 需要配对身份，上游使用配置文件关联 MAC、UDN 和名称。本实现可在接收端生成身份之后制作**本地模板**：

```powershell
.\.venv\Scripts\python.exe -m nxmini pairing-template --state work/receiver/state --mac 00:11:22:33:44:55 work/SAMSUNGAutoBackupDESC.ini
```

MAC 应为接收端实际网卡。该命令不写相机或卡。固件发现的路径是 `I:/dlna_web_root/SAMSUNGAutoBackupDESC.ini`，I 盘到可见存储的映射尚未确认，不能假定它就是 microSD 根目录。模板中的 WOL 字段用于兼容旧协议，接收端没有实现网络唤醒。首轮联调先备份并检查已存在的配置，再决定配对方法。

## 接收行为和 API

`CreateObject` 分配持久上传 ID，随后接收端按声明长度流式写入 `.incoming`。只在接收完整并计算 SHA-256 后发布为日期目录内文件，使用同文件系统硬链接避免覆盖已有文件。相同内容复用已校验文件，不同内容同名则追加 ID。重传从字节零开始，**没有宣称断点续传**；进程重启后仍保留 pending ID。相机端是否自动重试需要抓包验证。

支持 `.jpg / .jpeg / .srw / .mp4` 文件容器，单文件上限 16 GiB；这是接收端能力，不代表相机的每个传输模式都能发送这些格式。不会重编码、缩放或删卡。相机已完成备份后是否再次选择新文件，由相机自身状态决定，NAS 的去重独立执行。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /health` | 原型健康检查，始终报告相机未认证 |
| `GET /DMS/SamsungDmsDesc.xml` | 旧协议设备描述 |
| `POST /upnp/control/ContentDirectory1` | X_BACKUP_START／DONE、CreateObject 等 SOAP |
| `POST /cd/content?didx=0_id=...` | 相机上传；受相机 IP 白名单约束 |
| `GET /api/files?limit=100&before=...` | 带 Bearer 令牌，内容去重后的分页列表 |
| `GET /api/files/{sha256}` | 带令牌取回已完成原始文件 |

分页默认 100，最大 1000；响应的 `next_before` 为下一页游标，null 表示结束。下载由完整 SHA-256 标识，不受某一页范围限制。未知 SOAP 操作返回错误；SUBSCRIBE、chunked 上传、Range 下载和完整 DLNA 浏览服务尚未实现。家用局域网 HTTP 会明文传输图像和访问令牌，不应直接映射到公网。

## 群晖 DSM 7

本机没有 Docker 引擎，交付的是容器配置，尚未构建或部署到 NAS。源代码无基础第三方运行依赖，镜像以 Python 3.12 为基础。

1. 将源码放到 NAS 的独立目录；复制 `deploy/.env.example` 为 `deploy/.env`。
2. 设置真实的 NAS_IP、CAMERA_IP、NAS_UID、NAS_GID 和两个持久目录。预先创建照片／状态目录，授予该 UID/GID 写权限。示例 UID 1026 不能照搬。
3. 若使用 Synology Photos，先开启共享空间，将 PHOTO_DIR 指向该卷的 `photo/NXmini`。官方支持把文件直接写入 `/photo` 共享空间后由 Photos 管理；索引完成、日期和预览仍须在目标 NAS 上验收。[官方说明](https://kb.synology.com/en-nz/DSM/help/SynologyPhotos/managing_photos?version=7)
4. 在支持 Compose 的目标机运行：

```sh
docker compose --env-file deploy/.env -f deploy/compose.yaml config
docker compose --env-file deploy/.env -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env -f deploy/compose.yaml logs --tail 100
```

配置采用 host 网络以便 SSDP，不启用 privileged；只挂载 `/photos` 和 `/state`，根文件系统只读。相册索引可能需要时间；SRW 或视频能保存不等于 Synology Photos 支持其预览。数据库、令牌和身份位于 STATE_DIR，需一并备份。删除容器后重建应继续使用相同 STATE_DIR。

## Android 与 Quick Share

APK：`android/app/build/outputs/apk/debug/nxmini-bridge-debug.apk`，包名 `org.nxmini.evo`，最低 Android 10／API 29，目标 API 35。此为本地 debug 签名研究版，已经构建和验证 APK 签名，未安装到用户手机。

复现构建：

```powershell
.\.venv\Scripts\python.exe tools/bootstrap_analysis.py
.\.venv\Scripts\python.exe tools/bootstrap_android.py
.\.venv\Scripts\python.exe tools/build_android.py
```

也可用 Android Studio 打开 `android` 项目，使用 AGP 8.9.2／兼容的 Gradle 8.11.1／JDK 17 或更新版本。独立构建脚本不需要 Gradle，不自动接受 SDK 许可，不安装 APK，不接触手机。首次生成的本地 debug 密钥保存在忽略目录；重新生成密钥会改变 APK 签名。

在手机安装后填写接收端地址与 STATE_DIR 中的 `api-token` 内容。点击连接，每次加载 100 项，可加载较早文件。“保存原图”将 JPEG 放到 Pictures/NXmini、视频放到 Movies/NXmini、SRW 放到 Download/NXmini；只有长度及 SHA-256 校验通过才发布文件。“保存并分享”调用 Android Sharesheet，可在系统提供该目标时选择 Quick Share。存储接口采用 MediaStore，不请求全盘读写权限。[Android 存储接口](https://developer.android.com/training/data-storage/shared/media)

这条路径需要相机先成功发送到接收端，应用当前不能直接连接 MobileLink／AutoShare。Z Fold7 的折叠／展开、旋转、后台取消、权限提示以及分享接收端均未实测。One UI 版本不能替代实际系统 API 检查。

## 尚需验证的三条网络路线

| 路线 | 当前结论 | 下一步证据 |
| --- | --- | --- |
| MobileLink／AutoShare | 固件存在相关任务／字符串，独立客户端未实现 | 在相机 AP 上抓取实际 SSDP、描述、选择与传输请求；不预设 IP 和 API |
| 机内 FTP／HTTP | 未找到已验证执行入口与 RTOS 文件／socket 绑定 | 确认调用约定、任务栈、文件句柄、互斥和内存余量，再研究只读服务 |
| 机内 Quick Share | 尚无实现，不能等同于开放一个 HTTP／FTP 端口 | 研究发现、配对、加密传输和三星实现；RQuickShare 说明存在 BLE／mDNS 协作，也有手动发现路径，不能单凭蓝牙缺失判定绝对不可行。[项目说明](https://github.com/Martichou/rquickshare/blob/master/README.md) |

备用的 [emailservice](https://github.com/ge0rg/samsung-nx-emailservice) 上游已测试 NX mini，但需要对相机硬编码主机名做局域网 DNS 适配。尚未改动家庭 DNS 或部署该服务。该路线的原图尺寸、JPEG 压缩、EXIF、视频限制需将收到的文件与卡中原件逐项比较后记录。

只读发现命令：

```powershell
.\.venv\Scripts\python.exe -m nxmini discover --interface 192.168.1.10 --json work/discovery.json
.\.venv\Scripts\python.exe -m nxmini describe http://ACTUAL-CAMERA-IP/ACTUAL-DESCRIPTION.xml --json work/device-description.json
```

第二条命令中的地址必须来自实际发现，示例不是 NX mini 的已知接口。命令只读取描述，不发送拍摄或设置指令。
