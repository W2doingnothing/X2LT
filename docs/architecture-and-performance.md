# 技术栈与性能评估

评估日期：2026-11-15。以已发布 0.3.2 及 main 的测试路径修正为基线；本次只建立 develop 开发约定与评估，不实施语言迁移。

## 当前架构

| 层 | 技术 | 作用与约束 |
| --- | --- | --- |
| 桌面界面 | Python / Tkinter / ttk | 后台任务通过事件队列更新 UI；能力够用，复杂任务列表和现代样式需要更多手工处理 |
| X 数据提取 | gallery-dl 1.32.20 | Python 进程内集成，复用账号、点赞及媒体解析逻辑；存在配置全局锁，并适配其内部方法 |
| 文件传输 | requests / PySocks / ThreadPoolExecutor | 两个文件并发，每文件最多四路分段；分段 worker 内复用 Session，默认每次 Range 请求 1 MiB |
| 完成与恢复 | SQLite WAL / JSON / SHA256 | SQLite 保存队列和迁移状态，JSON 在原目录保存完成记录；迁移保留恢复日志 |
| 凭证 | Windows DPAPI / 目录 ACL | 针对 Windows 当前用户加密，无明文降级 |
| 分发 | PyInstaller onedir | 包含 Python、Tk 和运行依赖；当前 Windows ZIP 约 20 MB，ZIP 不是运行内存占用 |

## 优化前基线（0.3.2）

### 下载：先测协议与请求策略

源码中每个安全 Range 请求默认 1 MiB。100 MiB、均匀四路分段的理想情形约需 100 次范围响应，另有探测和重试。在高延迟代理下，请求响应次数可能形成开销；大小必须结合断流率取舍。现有 worker 已复用连接，不能把每次请求都描述成重新建立 TCP/TLS。

可对比 1/4/8/16 MiB 请求范围、自适应分块、不同连接数、连接/首字节/有效传输用时、重试及 429 次数。不能直接把连接数拉高或删除提取限速来保证提速。

requests 本身没有 HTTP/2 客户端能力；可以实验 HTTPX 的可选 HTTP/2，但要实际检查 CDN/代理协商结果，并回归 Range、重定向策略、SOCKS DNS、取消和资源校验器。HTTP/2 不是自动提速开关。[HTTPX 官方说明](https://www.python-httpx.org/http2/)

### 脱壳：存在重复整文件读取

对临时生成的 16 MiB 文件执行一次当前脱壳，统计到 5 次完整 SHA256 读取，共 80 MiB；另外复制读取 16 MiB，总应用层读取约 96 MiB。该次耗时 0.106 秒，文件很小且可能命中系统缓存，不能当作物理磁盘吞吐或大视频耗时预测。

重复来自迁移前检查、_move 中再次检查、复制后校验、目标确认和源文件删除前确认。优先消除重复前置检查；更进一步可设计同卷重命名和跨卷复制两条路径，并保持迁移日志与崩溃恢复。不能简单删除所有哈希步骤以换取速度。

SHA256 由 hashlib 的底层实现执行，不是逐字节的 Python 循环；大块更新会释放 GIL。因此换 Rust/C# 不一定显著加快这部分，减少不必要的数据读取更直接。[Python hashlib 官方文档](https://docs.python.org/3.14/library/hashlib.html)

### 队列与界面：全量读取随记录数增长

临时 SQLite 队列的 Store.list() 五次平均：1,000 条约 3.95 ms，10,000 条约 40.97 ms。仅含 Python/SQLite 全量物化，不含 Tk 更新、文件存在性查询；这是本机小样本，不是其他机器的保证。

当前调度循环和界面刷新会读取全量记录。可以优先改成按状态和保存范围查询、索引与分页、仅刷新改变的行、合并高频进度事件。SQLite/WAL 本身适合此规模，暂时没有改用服务器数据库的必要。

### 提取：维护接口比换语言更关键

X 解析依赖 gallery-dl。其配置为全局状态，所以当前有锁；还适配了 _init_session、_extract_media 等内部接口。升级提取器的兼容性和凭证隔离需要回归测试。

即使主程序换语言，初期仍建议复用该提取器。可以把它隔离成 Python 工作进程，通过受控 JSON/标准输入输出通信，凭证不放命令行或明文临时文件。这样仍需分发 Python 提取组件，不能宣称完全去掉 Python 或必然缩小总包体。

## 候选方案

以下是基于架构及官方能力的工程判断；未实现同功能的对照程序，未实测候选方案的内存、启动时间或下载吞吐，不能给出倍数排名。

| 方案 | 预期价值 | 成本与限制 | 适合本项目的情况 |
| --- | --- | --- | --- |
| 继续 Python + Tkinter，优化现有实现 | 复用现有功能与测试，最快验证请求、磁盘和队列优化 | UI 大改较费工；解释器仍需分发 | 当前首选，先解决可测量的瓶颈 |
| Python + PySide6 | 保留下载与提取代码，使用 Qt 控件、模型/视图重做任务界面 | 增加 Qt 依赖；不保证更小或更省内存，也不直接改变 CDN 速度 | 主要希望改善界面与交互时 |
| C# + .NET + WPF | Windows 桌面控件、异步网络/文件任务、类型检查与系统集成较合适 | WPF 仅 Windows；迁移队列、凭证与安全恢复需成本，提取器仍可需要 Python | 如果决定长期做 Windows 桌面产品，我优先考虑此项 |
| Rust + Tauri | Rust 适合控制下载/恢复状态，网页技术可做现代界面 | Rust/前端/提取工作进程多层维护；Windows 需 WebView2，进程总内存需一起测 | 明确需要跨平台与网页界面时 |
| Go + Wails | Go 适合网络并发服务，搭配网页 UI | 仍需前端、系统 WebView、提取器兼容层和重写恢复逻辑 | 团队熟悉 Go 或要复用服务端代码时 |

官方能力依据：[Qt for Python](https://doc.qt.io/qtforpython-6/)、[WPF 仅 Windows](https://learn.microsoft.com/en-us/dotnet/desktop/wpf/overview/)、[.NET HttpClient 连接池](https://learn.microsoft.com/en-us/dotnet/fundamentals/networking/http/httpclient-guidelines)、[Tauri 进程与 WebView 模型](https://v2.tauri.app/concept/process-model/)、[Tauri 外部工作进程](https://v2.tauri.app/develop/sidecar/)、[Wails 架构](https://wails.io/faq/)。

## 建议的后续顺序

1. 在 develop 建立阶段耗时、请求数、重试数、文件读写量和队列规模的可复现基线；网络对照使用授权样本及同一代理路径。
2. 优化重复哈希/复制与全量队列刷新，保持现有安全与恢复测试。
3. 实验自适应请求范围与可选 HTTP/2，按测量结果决定是否保留。
4. 按用户更看重的目标再选 UI 路线：界面升级可用 PySide6；Windows 长期产品可用 C# / WPF；跨平台再评估 Tauri 或 Wails。

不建议以“Rust/Go/C# 比 Python 快”为唯一理由全量重写。协议行为、代理/CDN、重复磁盘 I/O 和维护 X 解析的成本，会比语言微基准更直接影响这个工具。


## 0.4.0 已实现与验证

UI 0.3.3 先独立提交、构建；0.4.0 继续使用 Python/Tkinter，按磁盘、队列、请求策略的顺序优化。

- 正常脱壳从 5 次整文件哈希变为 4 次：16 MiB 文件的逻辑哈希读取从 80 MiB 降至 64 MiB，连同复制读取从 96 MiB 降至 80 MiB。复制后读回、目标校验、删除原文件前源校验仍在；不是物理磁盘 I/O 测量。已回归复制期间源变化、目标被改写、数据库及持久化失败、中断后收尾。
- 初始调度只取可运行任务 ID；之后按 ID 读取任务、新发现任务增量入队。状态事件合并按 ID 更新行，未变卡片保持原对象，避免每轮读取全部历史和重复检查已完成文件位置。统计在 SQLite 内聚合，未完成卡片每页最多 100。启动/显式完整刷新仍加载归档快照；超大归档的首次加载和 Treeview 内存尚未虚拟化。
- 请求策略新增 1/4/8 MiB，默认维持 1 MiB。单次 Range 更大仍保留分块读写、检查点与资源校验器；未提高连接上限。

本机脚本 `scripts/benchmark-download.py` 使用 16 MiB 合成数据、单连接、每个 GET 人工延迟 20 ms，包含探测请求。一次对照如下，时间会受机器和缓存影响：

| Range 大小 | GET 数量 | 耗时 | SHA256 |
| --- | --- | --- | --- |
| 1 MiB | 17 | 0.506 秒 | 一致 |
| 4 MiB | 5 | 0.243 秒 | 一致 |
| 8 MiB | 3 | 0.200 秒 | 一致 |

这验证减少请求往返的效果，不能外推为 Twitter CDN 或代理的提速倍数。真实网络仍需用户实际试用；建议稳定高延迟线路先试均衡 4 MiB，频繁断流则用保守。HTTP/2 与更换语言仍未实施。

复现（不访问账号、不使用系统代理）：

```powershell
.\.local-runtime\Scripts\python.exe scripts/benchmark-download.py --size-mb 16 --latency-ms 20
```
