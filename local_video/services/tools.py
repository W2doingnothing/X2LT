# SPDX-License-Identifier: GPL-2.0-only
"""Media organization and removal orchestration share the existing worker runtime."""
from pathlib import Path
import threading
from ..flatten import flatten_completed, rollback_completed
from ..runner import Runner


class MediaToolsService:
    def __init__(self, store, settings, libraries, runtime, native):
        self.store,self.settings,self.libraries,self.runtime,self.native=store,settings,libraries,runtime,native

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
        result=self.store.removal.prepare(p.get('ids'),self.libraries.manager.get(p['library_id'])['path'])
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
