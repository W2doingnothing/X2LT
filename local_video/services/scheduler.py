# SPDX-License-Identifier: GPL-2.0-only
"""Local-time download windows for the running desktop process."""
from datetime import datetime, timedelta
import json
from threading import Event, Thread
from time import time

from ..security import atomic_json


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
        self.after_tick(window is not None)
        if window is not None:
            self.run_download(self.attempted)

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


class XLikesAutomation:
    def __init__(self, settings, start_scan, scan_status, after_scan, stamp, *, clock=time):
        self.settings, self.start_scan, self.scan_status = settings, start_scan, scan_status
        self.after_scan, self.stamp, self.clock = after_scan, stamp, clock
        self.startup_due = settings()['x_scan_on_start']
        self.status = None
        self.active = None
        self.retry_at = 0
        try:
            last = json.loads(stamp.read_text(encoding='utf8'))
            self.last = last if isinstance(last, dict) else {}
        except (OSError, ValueError):
            self.last = {}

    def tick(self, opened, available, library_id):
        now = self.clock()
        if self.active is not None:
            self.status = self.scan_status()
            if not self.status or self.status['state'] == 'scanning':
                return
            scope, startup = self.active
            if self.status['state'] == 'ready':
                self.last = {'scope': scope, 'at': now}
                atomic_json(self.stamp, self.last)
                if startup:
                    self.startup_due = False
            else:
                self.retry_at = now + 60
            self.active = None
            if self.status['state'] == 'ready' or self.status.get('queued'):
                self.after_scan(scope[1])
        if not available or now < self.retry_at:
            return
        settings = self.settings()
        scope = [settings['username'].lstrip('@').casefold(), library_id]
        startup = self.startup_due and settings['x_scan_on_start']
        last = self.last.get('at') if self.last.get('scope') == scope else None
        timed = (opened and settings['x_scan_in_window']
                 and (type(last) not in (int, float) or last > now or now - last >= settings['x_scan_interval'] * 60))
        if startup or timed:
            try:
                if self.start_scan(library_id, settings['x_scan_limit']):
                    self.active = scope, startup
                    self.status = {'state': 'scanning'}
            except (OSError, ValueError) as exc:
                self.status = {'state': 'failed', 'error': str(exc)}
                self.retry_at = now + 60
