# SPDX-License-Identifier: GPL-2.0-only
"""Local-time download windows for the running desktop process."""
from datetime import datetime, timedelta
from threading import Event, Thread


def download_window(now, start, end):
    """Return the local date of the current window, or None outside it."""
    minute = now.strftime('%H:%M')
    if start < end:
        return now.date() if start <= minute < end else None
    if minute >= start:
        return now.date()
    if minute < end:
        return now.date() - timedelta(days=1)
    return None


class DesktopScheduler:
    def __init__(self, settings, set_window, run_download, after_tick=lambda opened: None, *, clock=datetime.now):
        self.settings = settings
        self.set_window = set_window
        self.run_download = run_download
        self.after_tick = after_tick
        self.clock = clock
        self.stop = Event()
        self.changed = Event()
        self.worker = None
        self.window = None
        self.config = None
        self.attempted = set()

    def tick(self):
        settings = self.settings()
        config = (settings['schedule_enabled'], settings['schedule_start'], settings['schedule_end'])
        enabled, start, end = config
        now = self.clock()
        window = download_window(now, start, end) if enabled else None
        if config != self.config or window != self.window:
            self.set_window(enabled, window is not None)
            self.attempted.clear()
            self.config, self.window = config, window
        if window is not None:
            self.run_download(self.attempted)
        self.after_tick(window is not None)

    def start(self):
        if not self.worker:
            self.worker = Thread(target=self._run, name='StashRat scheduler', daemon=True)
            self.worker.start()

    def _run(self):
        while not self.stop.is_set():
            self.changed.clear()
            try:
                self.tick()
            except (OSError, ValueError):
                # A temporarily unavailable library or connection can be retried next tick.
                pass
            self.changed.wait(3)

    def close(self):
        self.stop.set()
        self.changed.set()
        if self.worker:
            self.worker.join(timeout=.2)
        return self.worker is None or not self.worker.is_alive()
