# SPDX-License-Identifier: GPL-2.0-only
"""Media organization and removal orchestration share the existing worker runtime."""
from pathlib import Path
import threading
from ..auto_flatten import AutoFlatten
from ..flatten import flatten_completed, rollback_completed
from ..runner import Runner
from ..transfer import Paused


class MediaToolsService:
    def __init__(self, store, settings, libraries, runtime, native):
        self.store,self.settings,self.libraries,self.runtime,self.native=store,settings,libraries,runtime,native
        self.layout = AutoFlatten(store)
        self.telegram_busy = lambda: False

    def set_auto_flatten(self, p):
        if self.runtime.busy() or self.runtime.removing() or self.telegram_busy():
            raise ValueError('请先完成或暂停下载和文件操作')
        library = self.libraries.manager.get(p['library_id'])
        enabled = p['enabled']
        self._start_layout([(library, enabled)])
        return True

    def recover_auto_flatten(self):
        if self.runtime.busy():
            return
        libraries = self.libraries.manager.snapshot(cached_only=True)
        recovery = [(item, item.get('auto_flatten', False)) for item in libraries
                    if item['id'] not in self.libraries.errors and self.layout.needs_recovery(item)]
        if recovery:
            self._start_layout(recovery)

    def _start_layout(self, jobs):
        manager = self.libraries.manager
        reserved = []
        try:
            for item, enabled in jobs:
                identifier = item['id']
                manager.begin_layout(identifier)
                reserved.append(identifier)
                manager.save_layout(identifier, enabled, True)
            cancel = threading.Event()
            self.runtime.runner = type('Cancellation', (), {'cancel': cancel})()

            def emit(event):
                self.runtime.emit({**event, 'scope': 'organize'})

            def work():
                try:
                    while self.runtime.removing() and not cancel.wait(.05):
                        pass
                    for item, enabled in jobs:
                        if cancel.is_set():
                            break
                        error = ''
                        try:
                            result = self.layout.apply(item, enabled, cancel, emit)
                            moved, skipped, failed = (result[key] for key in ('moved', 'skipped', 'failed'))
                            if failed:
                                error = f'{failed} 个媒体文件整理失败，请查看日志后重试'
                            emit({'type': 'message', 'text': f'整理结束：移动 {moved} 个，跳过 {skipped} 个，失败 {failed} 个'})
                        except Paused as exc:
                            error = str(exc)
                        except Exception as exc:
                            error = self.settings.redact(str(exc))
                        try:
                            manager.save_layout(item['id'], enabled, bool(error), error)
                        except OSError as exc:
                            error = self.settings.redact(str(exc))
                        if error:
                            emit({'type': 'message', 'text': error})
                        emit({'type': 'changed'})
                finally:
                    for identifier in reserved:
                        manager.end_layout(identifier)
                    emit({'type': 'finished'})

            self.runtime.worker = self.runtime.thread_factory(target=work, daemon=True, name='media-layout')
            self.runtime.worker.start()
        except Exception:
            for identifier in reserved:
                manager.end_layout(identifier)
            raise

    def filters(self,p):return self.store.removal.page(p.get('query',''),p.get('offset',0))

    def clear_filters(self,p):
        count=self.store.removal.clear(None if p.get('all') is True else p.get('keys',[]))
        self.runtime.emit({'type':'changed'});return count

    def plan_removal(self,p):
        if not p.get('library_id'):raise ValueError('请选择移除文件所在的媒体库')
        plan=self.store.removal.plan(p.get('ids'),self.libraries.manager.get(p['library_id'])['path'])
        return {k:v for k,v in plan.items() if k!='rows'}

    def start_removal(self,p):
        if not p.get('library_id'):raise ValueError('请选择媒体库')
        if self.runtime.busy() and not isinstance(self.runtime.runner,Runner):raise ValueError('请先完成或暂停媒体转移')
        library = self.libraries.manager.get(p['library_id'])
        with self.store.lock:
            if self.layout.has_pending_move(library):raise ValueError('请先完成或重试自动集中')
            result=self.store.removal.prepare(p.get('ids'),library['path'])
        self.launch_removal(result['id']);self.runtime.emit({'type':'changed'});return result

    def retry_removal(self,p):
        self.store.removal.status(p.get('id'));self.launch_removal(p['id']);return True

    def recover_removals(self):
        with self.store.lock:
            pending=[r[0] for r in self.store.db.execute("SELECT DISTINCT operation FROM removal_items WHERE status='pending'")]
        for operation in pending:self.launch_removal(operation)

    def ignore(self,p):
        self.store.ignore_tweets(p['ids'],p.get('ignored',True));self.runtime.emit({'type':'changed'});return True

    def organize(self,p):
        if self.runtime.busy():raise ValueError('请先暂停下载')
        if any(t.is_alive() for t in self.runtime.removal_threads.values()):raise ValueError('请先等待媒体移除完成')
        library=self.libraries.manager.get(p.get('library_id'));output=Path(library['path']);target=output/'集中媒体'
        search=None
        if p.get('search'):
            search=self.native.choose_folder()
            if not search:return False
        if p.get('custom_target'):
            selected=self.native.choose_folder()
            if not selected:return False
            target=Path(selected)
        cancel=threading.Event();self.runtime.runner=type('Cancellation',(),{'cancel':cancel})()
        emit=lambda event:self.runtime.emit({**event,'scope':'organize'})
        def work():
            try:
                rows=self.store.completed_for_output(output)
                with self.libraries.manager.lease(target/'capacity.tmp') as lease:
                    lease.reserve(sum(r['bytes'] for r in rows)+16*1024**2)
                    result=rollback_completed(self.store,output,cancel,emit,search=search) if p.get('rollback') else flatten_completed(self.store,output,target,cancel,emit)
                emit({'type':'message','text':f"整理结束：移动 {result['moved']} 个，跳过 {result['skipped']} 个"})
            except Exception as exc:emit({'type':'message','text':self.settings.redact(str(exc))})
            finally:emit({'type':'finished'})
        self.runtime.worker=self.runtime.thread_factory(target=work,daemon=True);self.runtime.worker.start();return True


    def launch_removal(self,operation):
        previous=self.runtime.removal_threads.get(operation)
        if previous and previous.is_alive():return
        runner=self.runtime.runner if isinstance(self.runtime.runner,Runner) else None
        def work():
            try:
                result=self.store.removal.process(operation,runner=runner)
                self.runtime.emit({'type':'message','text':f"移除完成 {result['done']}/{result['total']}；失败 {result['failed']}，可在过滤媒体名单重试"})
            except Exception as exc:self.runtime.emit({'type':'message','text':self.settings.redact(str(exc))})
            finally:
                self.libraries.manager.invalidate_capacity()
                self.runtime.emit({'type':'changed'})
        thread=self.runtime.thread_factory(target=work,daemon=True,name='media-removal')
        self.runtime.removal_threads[operation]=thread;thread.start()
