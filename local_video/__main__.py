# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
import argparse
import json
from pathlib import Path
import tempfile


def main():
    parser = argparse.ArgumentParser(description='X2T 本地视频下载器')
    parser.add_argument('--self-check', metavar='REPORT', help='不访问账号，只检查打包依赖和 DPAPI，并保存报告')
    parser.add_argument('--smoke-gui', metavar='PRIVATE_DIR', help='在指定测试目录创建窗口后退出，不读取账号')
    args = parser.parse_args()
    if args.self_check:
        from gallery_dl import version
        from gallery_dl.extractor import find
        import socks
        from PIL import Image
        from io import BytesIO
        for fmt in ("JPEG", "PNG", "WEBP"):
            stream = BytesIO()
            Image.new("RGB", (2, 2)).save(stream, format=fmt)
            stream.seek(0)
            with Image.open(stream) as image:
                image.load()
        from .security import protect, unprotect
        from . import __version__
        assert unprotect(protect(b'local-self-check')) == b'local-self-check'
        assert find('https://x.com/example/status/123') is not None
        Path(args.self_check).write_text(json.dumps({'version': __version__, 'gallery_dl': version.__version__, 'socks': True, 'dpapi': True, 'pillow_formats': ['JPEG','PNG','WEBP']}, indent=2), encoding='utf8')
        return 0
    if args.smoke_gui:
        diagnostic = Path(args.smoke_gui)
        diagnostic.mkdir(parents=True, exist_ok=True)
        try:
            import tkinter as tk
            from .app import App
            root = tk.Tk()
            app = App(root, data_dir=diagnostic)
            from PIL import Image, ImageTk
            sample = ImageTk.PhotoImage(Image.new("RGB", (2, 2)), master=root)
            assert sample.width() == 2 and sample.height() == 2
            del sample
            root.after(800, app.close)
            root.mainloop()
            (diagnostic / 'smoke-result.json').write_text('{"success": true, "pillow_tk": true}', encoding='utf8')
            return 0
        except Exception as exc:
            from .security import redact
            import os
            import sys
            tcl = Path(getattr(sys, '_MEIPASS', '.')) / '_tcl_data' / 'init.tcl'
            details = {'success': False, 'error': type(exc).__name__, 'message': redact(exc), 'tcl_exists': tcl.is_file(), 'tcl_env': os.environ.get('TCL_LIBRARY')}
            if tcl.is_file():
                details['tcl_bytes'] = tcl.stat().st_size
            (diagnostic / 'smoke-result.json').write_text(json.dumps(details, ensure_ascii=False), encoding='utf8')
            return 1
    from .app import main as gui_main
    return gui_main()


if __name__ == '__main__':
    raise SystemExit(main())
