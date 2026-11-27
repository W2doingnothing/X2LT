# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
from local_video.__main__ import main

if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        import ctypes
        from local_video.security import redact
        ctypes.windll.user32.MessageBoxW(None,'StashRat 无法启动：'+redact(exc)+'\n\n请确认已完整解压，并安装 Microsoft Edge WebView2 Runtime。', 'StashRat',16)
        raise SystemExit(1)
