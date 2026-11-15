# 开发与发布

提交问题只附脱敏的错误、版本和复现步骤，不提交 Cookie、代理密码、队列、下载 JSON 或视频。

## 分支

`main` 保留稳定发布代码，日常开发使用 `develop`。需要隔离任务时从 develop 创建功能分支，完成后合回 develop。合并 main 和发布版本另行确认。开发前检查工作区和当前分支，完整约定见 AGENTS.md。

当前技术栈与性能评估见 [架构评估](docs/architecture-and-performance.md)。

## 验证修改

在 Windows 安装运行依赖后执行：

```powershell
.\.local-runtime\Scripts\python.exe -m unittest discover -s tests -v
```

测试只用临时目录、合成视频和本机 HTTP 服务，不使用实际账号和下载队列。

## 每次更新

1. 用户可见修改写入 CHANGELOG.md 的“未发布”。
2. 发版时更新 local_video/__init__.py 的 __version__，把记录归入同名版本及实际日期。
3. 同步 README 操作说明，行为变化增加测试，运行全套测试。
4. 依赖变更时更新依赖文件、锁定版本及 THIRD_PARTY_NOTICES.md，准备对应源码，保留许可证。
5. 检查 git diff --cached 与发布清单；gitignore 不会取消已经跟踪的文件，也不代替隐私检查。

## Windows 发布

安装 requirements-build.txt（引用完整锁定依赖）后，先提交源码再运行 build-local-video.ps1。构建记录 Git 提交、版本与所有运行文件 SHA256；打包时拒绝未提交修改、依赖漂移、旧构建或被修改的运行文件。图标由 scripts/create-icon.ps1 从项目自有几何图形生成。

仅从明确清单组装下载包：EXE、local-video-runtime、启动脚本、README、CHANGELOG、LICENSE、第三方声明及许可证。不要直接打包工作目录。

同时发布该 Git 标签的 X2LT 源码 ZIP、锁定版本的第三方源码包和 SHA256SUMS.txt。先用包内 EXE 执行 --self-check 和临时目录的 --smoke-gui，通过后再上传。

完成源码提交后，在项目目录运行：

```powershell
.\.local-runtime\Scripts\python.exe scripts/prepare-release.py --fetch-third-party
.\.local-runtime\Scripts\python.exe scripts/prepare-release.py
```

文件生成于忽略目录 `.release/`。第三方源码下载只访问公开 PyPI，核对元数据提供的 SHA256。发布前核对包内版本和当前 Git 提交；打包脚本不会替你提交或上传。

确保版本、Git 标签、更新记录和下载包名称一致；更新记录只描述实际完成的内容。
