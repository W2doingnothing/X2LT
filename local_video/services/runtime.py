# SPDX-License-Identifier: GPL-2.0-only
"""Existing desktop worker/event lifecycle, independent of the window."""
import queue
import threading
from ..operations import OperationState


class WorkerRuntime:
    def __init__(self, thread_factory=threading.Thread):
        self.thread_factory = thread_factory
        self.runner = self.worker = None
        self.removal_threads = {}
        self.operation = OperationState()
        self.events = queue.Queue(maxsize=2000)
        self.cancel = threading.Event()

    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def removing(self):
        return any(t.is_alive() for t in self.removal_threads.values())

    def emit(self, event):
        self.operation.update(event)
        try: self.events.put_nowait(event)
        except queue.Full:
            try: self.events.get_nowait()
            except queue.Empty: pass
            try: self.events.put_nowait({'type':'resync'})
            except queue.Full: pass

    def poll(self):
        events=[]
        for _ in range(500):
            try: events.append(self.events.get_nowait())
            except queue.Empty: break
        return {'events':events,'busy':self.busy(),'operation':self.operation.snapshot()}

    def pause(self):
        if self.runner: self.runner.cancel.set()
        return True

    def close(self):
        if self.removing(): return False
        if self.busy():
            self.pause(); self.worker.join(timeout=1)
            if self.worker.is_alive(): return False
        self.cancel.set()
        return True
