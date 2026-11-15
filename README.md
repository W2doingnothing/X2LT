# X2LT

**X to Local or Telegram**，一个 Windows 桌面视频归档工具。

当前版本支持将 X / Twitter 的点赞视频和单条推文视频保存到本地。Telegram 上传尚未实现。

[下载 Windows 版](https://github.com/W2doingnothing/X2LT/releases/latest) · [更新记录](CHANGELOG.md) · [反馈问题](https://github.com/W2doingnothing/X2LT/issues)

## 功能

- 下载最近点赞视频、全部点赞视频，或粘贴推文链接下载。
- 最高画质、720p、480p；最多同时两个视频，每文件最多四路分段。
- 暂停续传、失败重试、长度与 SHA256 校验。
- 一键脱壳集中视频，可回退至原目录，也可按哈希查找手动移动的视频。
- JSON 完成记录留在原目录，视频被移走后仍跳过重复下载。
- Cookie 和含密码的代理配置用 Windows DPAPI 加密保存。

仅下载平台原生视频，不包含图片、GIF 或外链视频。读取点赞需要登录。

## 下载与启动

1. 从 [Releases](https://github.com/W2doingnothing/X2LT/releases/latest) 下载 `X2LT-版本号-windows-x64.zip`。
2. 完整解压到可写目录，双击 **本地视频下载器.exe**，也可运行 **start-local-video.cmd**。
3. 保留旁边的 **local-video-runtime** 文件夹，它是程序所需的运行库。

发布环境为 Windows 10/11 x64，下载包未进行代码签名。可用同一 Release 的 `SHA256SUMS.txt` 检查文件完整性。

## 界面

0.3.3 起采用卡片式总览和下载任务页，左侧切换归档记录、视频整理和设置。右上角可切换浅色/深色并自动记住选择。点击“新建下载”粘贴链接或同步点赞；账号、代理、保存目录和画质在设置页。账号卡片只表示凭证填写状态，不代表已验证联网。

## 账号与代理

填写 X 用户名、`auth_token`、`ct0`，点击“保存加密凭证”。这两个 Cookie 可在自己已登录 X 的浏览器开发者工具中查看，属于 `x.com`。它们相当于登录凭证，请勿分享或提交到 Issue。

代理示例：`http://127.0.0.1:7890`、`socks5://127.0.0.1:7890`。端口请使用代理软件实际监听值。SOCKS5 使用远端 DNS；留空表示直连，不自动继承系统代理。

设置和队列位于 `%LOCALAPPDATA%\X2TLocalVideo`，该目录名为兼容已有记录保留。凭证用当前 Windows 用户的 DPAPI 加密；更换电脑或账号后通常需要重新填写。DPAPI 不防御同一用户权限下运行的恶意程序。

## 下载、暂停与校验

选择保存目录、画质、连接数和大小上限，再点击下载。

- “最新点赞”读取最近 50 条含媒体的推文窗口，不保证得到 50 个视频。
- 文件默认保存为 `保存目录/画质/作者/推文ID/推文ID_媒体序号.mp4`，各画质分开保存。
- 暂停会保留 `.parts` 分段；下次点“继续未完成 / 重试”。关闭窗口也会先暂停，当前请求可能需约 20 秒结束。
- 仅在 Range、已知长度和可靠资源校验器都可用时启用安全分段与跨次续传；否则自动单流，暂停后可能需要重下。
- “校验已完成文件”只检查本地文件，不自动补下载，缺失或损坏仍保留历史。
- 补回视频时选中条目，点“强制重新下载选中项”；强制任务暂停后仍可继续。

SHA256 是本地一致性记录，不是平台内容签名，也不保证完整解码。缺少服务器总长度会明确提示。若系统 PATH 中有 ffprobe，会额外检查视频流；发布包不附带 ffprobe。

下载分段、合并与正式文件可能同时存在，需要额外磁盘空间。

## 脱壳、回退与防重复

“一键脱壳 · 集中视频”把当前保存范围内的已完成视频移到 `保存目录/集中视频`；也可用“选择脱壳目录…”指定位置。

- 仅移动 MP4，`.verified.json` 留在原分层目录，记录视频标识、画质、大小、SHA256 和最近位置。
- 有完成记录时，即使视频被手动移动或删除，再次同步仍跳过下载。JSON 表示曾下载完成，不证明文件目前存在。
- 保留原目录中的 JSON 和应用队列。**顶部“保存目录”保持原值**，改变它会创建另一个归档范围，可能重复下载。
- 新视频仍按原层级保存，之后可再次脱壳。

“脱壳回退”恢复原目录。视频被手动移动或改名后，用“查找并回退…”选择它所在的文件夹，程序只在该文件夹及子目录按大小和 SHA256 查找，不扫描全盘。

迁移先复制校验、保存位置记录，再清理旧副本。异内容同名文件不覆盖；暂停或失败优先保留数据，再次操作可收尾。旧版本随视频移动的 JSON 会安全归回原目录，无法确认归属的文件保留并提示。

## 速度调整

设置中的“请求策略”提供保守 1 MiB（默认）、均衡 4 MiB、吞吐 8 MiB。高延迟且连接稳定时可尝试均衡；断流较多时保守通常更合适。单次请求变大不会增加连接数，也不保证实际提速。

默认四个连接。若重置较多或更慢，可试一个或两个；切换连接数可能使旧分段无法复用。720p / 480p 从实际提供的版本选择，竖屏按短边限制。

提取与下载均使用填写的代理，视频直接来自平台 CDN。实际速度取决于节点到 CDN 的吞吐，多连接不保证固定倍数提速。

## 源码运行与构建

需要 Windows、Python 3.11+ 和 Tkinter；当前发布构建使用 Python 3.14.3 x64。

```powershell
python -m venv .local-runtime
.\.local-runtime\Scripts\python.exe -m pip install -r requirements-local-video.txt
.\.local-runtime\Scripts\python.exe launch_local_video.py
```

也可运行 `setup-local-video.cmd` 后用 `start-local-video.cmd` 启动。

```powershell
.\.local-runtime\Scripts\python.exe -m unittest discover -s tests -v
.\.local-runtime\Scripts\python.exe -m pip install -r requirements-build.txt
.\build-local-video.ps1
```

完整构建依赖版本在 `requirements-local-video-lock.txt`。发布流程见 [CONTRIBUTING.md](CONTRIBUTING.md)，依赖及对应源码见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 许可证

Copyright (C) 2026 W2doingnothing。X2LT 源码采用 [GPL-2.0-only](LICENSE)，第三方组件保留各自的许可证和版权声明。

请只保存你有权下载的内容；反馈问题不要附上 Cookie、代理密码、私人视频或完整下载记录。
