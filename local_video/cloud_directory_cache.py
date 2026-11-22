# SPDX-License-Identifier: GPL-2.0-only
"""Account-scoped directory pages and optional breadth-first prefetch for one app run."""
from collections import OrderedDict, deque
from concurrent.futures import Future
import threading


class CloudDirectoryCache:
    def __init__(self, fetch, delay=0.15, max_pages=8192, max_items=150_000):
        self.fetch = fetch
        self.delay = delay
        self.max_pages, self.max_items = max_pages, max_items
        self.lock = threading.RLock()
        self.account = None
        self.epoch = 0
        self.pages = OrderedDict()
        self.item_count = 0
        self.revisions = {}
        self.inflight = {}
        self.enabled = False
        self.closed = False
        self.worker = None
        self.cancel = threading.Event()

    def set_account(self, account):
        with self.lock:
            if account == self.account:
                return
            self.cancel.set()
            self.account = account
            self.epoch += 1
            self.pages.clear()
            self.revisions.clear()
            self.item_count = 0
            self._start()

    def set_enabled(self, enabled):
        with self.lock:
            if enabled == self.enabled:
                return
            self.enabled = enabled
            self.cancel.set()
            self._start()

    def _start(self):
        if not self.enabled or not self.account or self.closed:
            return
        self.cancel = threading.Event()
        self.worker = threading.Thread(target=self._prefetch, args=(self.account, self.cancel),
                                       name='x2lt-cloud-directory-prefetch', daemon=True)
        self.worker.start()

    def _remove(self, key):
        old = self.pages.pop(key, None)
        if old is not None:
            self.item_count -= len(old['items'])

    def page(self, account, parent, page, refresh=False):
        key = parent, page
        with self.lock:
            if self.closed or account != self.account or not account:
                raise ValueError('光鸭账号已改变，请重新打开目录')
            if not refresh and key in self.pages:
                self.pages.move_to_end(key)
                return self.pages[key]
            flight = self.epoch, key
            future = self.inflight.get(flight)
            if future is None:
                future = self.inflight[flight] = Future()
                owner = True
                revision = self.revisions.get(parent, 0)
            else:
                owner = False
        if not owner:
            return future.result()
        try:
            result = self.fetch(parent, page)
            with self.lock:
                if self.closed or flight[0] != self.epoch:
                    raise ValueError('光鸭账号已改变，请重新打开目录')
                if revision == self.revisions.get(parent, 0):
                    if refresh:
                        for existing in list(self.pages):
                            if existing[0] == parent:
                                self._remove(existing)
                        self.revisions[parent] = revision + 1
                    self._remove(key)
                    self.pages[key] = result
                    self.item_count += len(result['items'])
                    while len(self.pages) > self.max_pages or self.item_count > self.max_items:
                        self._remove(next(iter(self.pages)))
                future.set_result(result)
            return result
        except Exception as exc:
            future.set_exception(exc)
            raise
        finally:
            with self.lock:
                self.inflight.pop(flight, None)

    def _prefetch(self, account, cancel):
        pending, scheduled = deque(['']), {''}
        requests = 0
        while pending and not cancel.is_set() and requests < self.max_pages:
            parent = pending.popleft()
            for page in range(self.max_pages):
                if cancel.is_set() or requests >= self.max_pages:
                    return
                try:
                    result = self.page(account, parent, page)
                except (ValueError, OSError):
                    break
                requests += 1
                for entry in result['items']:
                    if entry['type'] == 'directory' and entry['id'] not in scheduled:
                        scheduled.add(entry['id'])
                        pending.append(entry['id'])
                if cancel.wait(self.delay):
                    return
                if not result.get('has_more'):
                    break

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel.set()
