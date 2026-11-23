# SPDX-License-Identifier: GPL-2.0-only
"""Explicit native operations; never exposed as a JavaScript API object."""
import os
from pathlib import Path
import webbrowser


class NativeHost:
    def __init__(self):
        self.window = None
        self.approved_paths = set()

    def choose_folder(self):
        if self.window is None: raise ValueError('文件夹选择器仅桌面版可用')
        import webview
        result=self.window.create_file_dialog(webview.FileDialog.FOLDER)
        if not result: return None
        path=str(Path(result[0]).resolve())
        self.approved_paths.add(path)
        return path

    def choose_image(self):
        if self.window is None: raise ValueError('图片选择器仅桌面版可用')
        import webview
        result=self.window.create_file_dialog(webview.FileDialog.OPEN,allow_multiple=False,file_types=('图片 (*.jpg;*.jpeg;*.png;*.webp)',))
        return result[0] if result else None

    def choose_local_import(self, kind, selection, single=False):
        if self.window is None:
            raise ValueError('本地文件选择器仅桌面版可用')
        import webview
        from ..local_import_files import COMIC_EXTENSIONS, IMAGE_EXTENSIONS, VIDEO_EXTENSIONS

        if selection == 'folder':
            result = self.window.create_file_dialog(webview.FileDialog.FOLDER)
        else:
            extensions = COMIC_EXTENSIONS if kind == 'comic' else IMAGE_EXTENSIONS | VIDEO_EXTENSIONS
            patterns = ';'.join(f'*.{extension}' for extension in sorted(extensions))
            label = '漫画压缩包' if kind == 'comic' else '图片和视频'
            result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=not single,
                                                    file_types=(f'{label} ({patterns})',))
        return (result,) if isinstance(result, str) else tuple(result or ())

    def open_url(self, url):
        webbrowser.open(url)
        return True

    def choose_upload_files(self):
        if self.window is None:
            raise ValueError('文件选择器仅桌面版可用')
        import webview
        result = self.window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True)
        return (result,) if isinstance(result,str) else tuple(result or ())

    def choose_cloud_backup(self, save):
        if self.window is None:
            raise ValueError('备份文件选择器仅桌面版可用')
        import webview
        options = {'save_filename': 'X2LT-光鸭索引.json'} if save else {'allow_multiple': False}
        result = self.window.create_file_dialog(webview.FileDialog.SAVE if save else webview.FileDialog.OPEN,
                                               file_types=('光鸭索引 (*.json)',), **options)
        if isinstance(result, str):
            return Path(result)
        return Path(result[0]) if result else None

    def open_file(self, path):
        if not path.exists(): raise ValueError('本地文件不存在')
        os.startfile(str(path))
        return True
