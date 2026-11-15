# 第三方组件与对应源码

X2LT 自有代码与几何 XL 图标由 W2doingnothing 于 2026 年发布，采用 GPL-2.0-only。第三方代码未改许可证，版权与具体授权以 third-party-licenses 中的原文为准。

## Python 组件

| 组件 | 本次构建版本 | 许可证/用途 |
| --- | --- | --- |
| gallery-dl | 1.32.20 | GPL-2.0-only，媒体信息提取 |
| requests | 2.32.5 | Apache-2.0，HTTP |
| PySocks | 1.7.1 | BSD，SOCKS 代理 |
| certifi | 2026.10.23 | MPL-2.0，CA 证书 |
| charset-normalizer | 3.5.2 | MIT，字符编码 |
| idna | 3.20 | BSD-3-Clause，域名处理 |
| urllib3 | 2.8.0 | MIT，HTTP 连接 |
| setuptools | 84.2.1 | MIT 及所附第三方声明，打包/运行支持 |
| packaging | 26.3 | Apache-2.0 或 BSD-2-Clause，版本处理 |
| PyInstaller | 6.22.3 | GPL-2.0-or-later，含 Bootloader Exception |
| pyinstaller-hooks-contrib | 2026.10 | 随附许可证，构建 hooks |
| altgraph | 0.17.5 | MIT，构建 |
| pefile | 2024.8.26 | MIT，构建 |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause，构建 |

部分组件只参与构建，不作为独立运行模块分发。PyInstaller 的 bootloader 例外见 pyinstaller-COPYING.txt。

## 解释器和本机运行库

Windows 包带有 CPython 3.14.3 x64、Tcl/Tk 8.6.15 和 Python 发行版所附本机依赖。Python-LICENSE.txt 包含 Python 许可及 OpenSSL、libffi 等随附组件声明；Tcl/Tk 许可证另附。

Python 包还包含 Microsoft Distributable Code（Visual C++/Universal CRT 运行库），该部分按 Python-LICENSE.txt 中的再分发条件提供，仅适用于 Microsoft 平台。不要移除、修改这些组件的版权/商标声明，不得将它们用于恶意、欺骗性或非法软件，也不得暗示 Microsoft 为本项目背书。这些限制适用于相应 Microsoft 组件，不改变 X2LT 自有源码的 GPL 许可。

解释器对应源码：[CPython v3.14.3](https://github.com/python/cpython/tree/v3.14.3)；[Tcl 8.6.15](https://github.com/tcltk/tcl/tree/core-8-6-15)；[Tk 8.6.15](https://github.com/tcltk/tk/tree/core-8-6-15)。运行库未作本地修改。

## 获取发布对应源码

每次 Release 同时提供：

- X2LT-版本-source.zip：该版本 Git 提交的完整项目源码、构建与依赖文件。
- X2LT-版本-third-party-sources.zip：锁定 Python 依赖的原始 sdist，包含 gallery-dl 对应源码和构建组件。SOURCES.json 记录原始下载 URL、版本和 SHA256，文件均已核验。
- SHA256SUMS.txt：Windows 包与源码包的 SHA256。

源码包可在 [同版本 Release](https://github.com/W2doingnothing/X2LT/releases) 中获取。项目构建命令见 README 与 CONTRIBUTING；scripts/prepare-release.py 使用固定版本的 PyPI 元数据准备对应源码及发布清单，不读取用户配置。

许可证文件不授予视频内容的使用权。下载内容的权利由相应权利人和平台条款决定。
