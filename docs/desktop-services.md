# 0.7.7 桌面服务边界

## 入口和服务

`DesktopService` 是组装入口，不承担业务 SQL、路径迁移、下载任务执行或凭证写入；JavaScript 只能调用 `call(action, payload)`。私有兼容属性仅供原生启动与已有诊断使用，不公开整个服务对象。

| 模块 | 职责与主要依赖 |
| --- | --- |
| `services/settings.py` | 设置校验与原子保存、DPAPI Vault、凭证脱敏、旧配置导入。公开设置仅白名单字段。 |
| `services/libraries.py` | 媒体库生命周期、原生选择器批准路径、重新定位和完成回执路径修复。沿用 LibraryManager 与 Store。 |
| `services/queries.py` | 内容/作者/来源/标签/资源查询、快照拼装与错误脱敏。分页仍读取容量缓存。 |
| `services/downloads.py` | 下载命令校验、输出与凭证快照、现有 Runner 启动与 run_id 事件。 |
| `services/tools.py` | 转移/回退、过滤/移除、失败重试及重启恢复，沿用 RemovalManager 与转移实现。 |
| `services/media.py` | 预览/头像/封面、受限系统打开、两路预览并发控制。 |
| `services/runtime.py` | 单一前台后台操作槽、移除线程、暂停/关闭协调、操作状态与事件队列。尚未改造成多队列调度器。 |
| `services/native.py` | 文件夹/图片选择、系统浏览器/播放器；窗口依赖集中在此。 |
| `bridge_contracts.py` | 固定命令白名单、字段/类型校验与请求副本；响应类型定义。 |

服务不依赖 DesktopService，也不导入 React/Tk 控件。多服务共用既有 Store/LibraryManager 和 WorkerRuntime，不复制配置或任务状态；服务直接复用领域实现，不经控制器互相转发。

## 接口保持兼容

成功为 `{"ok": true, "data": ...}`，失败为 `{"ok": false, "error": "已脱敏消息"}`。不添加 HTTP 端口或新的 JavaScript 暴露入口。

| 命令族 | 返回数据 |
| --- | --- |
| `snapshot` | 原快照字段：版本、公开设置、媒体库、内容/作者/来源分页、计数、操作状态等。 |
| `resources` / `resource_overview` | 原资源分页和总览统计；位置错误脱敏。 |
| `poll` | 最多 500 条事件、busy 与 operation；溢出继续发送 resync。 |
| `settings` | 公开设置白名单；不含 Cookie、代理或 Vault 内容。 |
| `credentials` / `import_legacy` | 成功布尔值；不回显凭证。 |
| 媒体库、标签、作者操作 | 保留原成功值、库对象或可撤销偏好快照。 |
| `removal_*` / `filters*` | 保留计划、操作结果及过滤分页；不接受前端传入任意删除路径。 |
| `preview` / `avatar` / `cover` | 受管理的图像 data URL 或 null；不暴露源 URL。 |
| `start` / `pause` / `organize` / 打开操作 | 原成功布尔值；任务进度继续从事件查询。 |

`None` 载荷仍视为无参数请求；False、0、空字符串或数组不再被误当成空对象。字段不隐式转换类型：布尔不能充当任务 ID/连接数，字符串不能充当布尔开关。未知字段统一返回格式错误，不在错误消息中回显值。

## 并发与保障

- 桌面控制变更继续由原控制锁串行化；预览不占控制锁，以两路信号量限制并发。
- 下载工作函数捕获创建时的 Runner；修改默认库、设置或凭证不改变已有任务配置。
- 共享 Runtime 保留下载与转移互斥、移除定向停止/等待、关闭等待及 run_id 语义，不另建调度器。
- 路径批准、URL 白名单、SHA 校验、去重与移除代次仍由原实现执行。
- 此版不迁移数据库，不接入新平台，也不宣称框架拆分能直接提升网络下载速度。
