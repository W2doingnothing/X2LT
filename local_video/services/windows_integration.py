# SPDX-License-Identifier: GPL-2.0-only
"""Windows per-user startup and native notification-area controls."""
from pathlib import Path
import subprocess
import sys
from threading import Thread


STARTUP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def startup_command():
    executable = Path(sys.executable).resolve()
    if getattr(sys, 'frozen', False):
        return subprocess.list2cmdline([str(executable)])
    script = Path(__file__).resolve().parents[2] / 'launch_local_video.py'
    quiet = executable.with_name('pythonw.exe')
    return subprocess.list2cmdline([str(quiet if quiet.is_file() else executable), str(script)])


def set_startup(enabled):
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, 'StashRat', 0, winreg.REG_SZ, startup_command())
        else:
            try:
                winreg.DeleteValue(key, 'StashRat')
            except FileNotFoundError:
                pass


class DesktopTray:
    def __init__(self, window, service, icon_path):
        self.window, self.service, self.icon_path = window, service, icon_path
        self.icon = None
        self.exiting = False
        self.exit_timer = None

    def setup(self):
        from System import Action
        from System.Drawing import Icon
        from System.Windows.Forms import ContextMenuStrip, NotifyIcon

        def create():
            if self.icon is not None or self.exiting:
                return
            menu = ContextMenuStrip()
            icon = NotifyIcon()
            icon.Icon = Icon(self.icon_path())
            icon.Text = 'StashRat'
            icon.ContextMenuStrip = menu
            for title, callback in (('打开 StashRat', self.show),
                                    ('暂停全部下载', lambda: self._background(self.service.pause_all_downloads)),
                                    ('继续下载', lambda: self._background(self.service.continue_downloads)),
                                    ('退出', self.exit)):
                menu.Items.Add(title).Click += lambda sender, event, run=callback: run()
            icon.DoubleClick += lambda sender, event: self.show()
            icon.Visible = True
            self.icon = icon

        self.window.native.Invoke(Action(create))

    def _background(self, callback):
        def run():
            try:
                callback()
            except (ValueError, OSError) as exc:
                from System import Action
                from System.Windows.Forms import ToolTipIcon
                message = str(exc)
                self.window.native.BeginInvoke(Action(lambda: self.icon and self.icon.ShowBalloonTip(
                    4000, 'StashRat', message, ToolTipIcon.Warning)))

        Thread(target=run, name='StashRat tray action', daemon=True).start()

    def show(self):
        from System.Windows.Forms import FormWindowState
        native = self.window.native
        native.Show()
        if native.WindowState == FormWindowState.Minimized:
            native.WindowState = FormWindowState.Normal
        native.Activate()

    def refresh(self):
        if self.icon is None:
            return
        from System import Action
        from System.Drawing import Icon

        def replace():
            if self.icon is not None:
                old = self.icon.Icon
                self.icon.Icon = Icon(self.icon_path())
                old.Dispose()

        self.window.native.Invoke(Action(replace))

    def closing(self):
        if self.icon is not None and self.service._settings['close_to_tray'] and not self.exiting:
            self.window.native.Hide()
            return False
        if not self.service._close():
            return False
        if self.exit_timer is not None:
            self.exit_timer.Stop()
            self.exit_timer.Dispose()
            self.exit_timer = None
        if self.icon is not None:
            self.icon.Visible = False
            self.icon.ContextMenuStrip.Dispose()
            self.icon.Icon.Dispose()
            self.icon.Dispose()
            self.icon = None
        return True

    def exit(self):
        if self.exiting:
            return
        from System.Windows.Forms import Timer

        self.exiting = True
        self.exit_timer = Timer()
        self.exit_timer.Interval = 500
        self.exit_timer.Tick += lambda sender, event: self.window.native.Close()
        self.exit_timer.Start()
        self.window.native.Close()
