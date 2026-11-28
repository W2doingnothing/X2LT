# SPDX-License-Identifier: GPL-2.0-only
"""Download commands use existing Runner and capture per-run configuration."""
from pathlib import Path

from ..policy import likes_url, validate_source_url
from ..runner import Runner
from ..security import atomic_json


class DownloadService:
    def __init__(self, store, settings, libraries, runtime, runner_factory=Runner):
        self.store,self.settings,self.libraries,self.runtime=store,settings,libraries,runtime
        self.runner_factory=runner_factory
        self.download_worker = None
        self.likes_worker = None
        self.likes_result = None
        self.hold_path = settings.path.with_name('x-download-hold.json')
        self.manual_hold = self.hold_path.exists()

    def set_manual_hold(self, held):
        if held != self.manual_hold:
            if held:
                atomic_json(self.hold_path, {'paused': True})
            else:
                self.hold_path.unlink(missing_ok=True)
            self.manual_hold = held

    def downloading(self):
        return self.download_worker is self.runtime.worker and self.runtime.busy()

    def scanning_likes(self):
        return self.likes_worker is self.runtime.worker and self.runtime.busy()

    def run_settings(self,library_id):
        library=self.libraries.manager.get(library_id)
        if library['id'] in self.libraries.errors:raise ValueError(self.libraries.errors[library['id']])
        return {**self.settings.values,'output':library['path'],'proxy':self.settings.credentials.get('proxy',''),'_storage_guard':self.libraries.manager}


    def start_likes_scan(self, library_id, limit):
        if self.runtime.busy():
            return False
        self.start({'mode': 'latest', 'library_id': library_id}, automatic=True, scan_limit=limit)
        return True

    def likes_status(self):
        if self.scanning_likes():
            return {'state': 'scanning'}
        return self.likes_result

    def start(self,p,*,automatic=False,scan_limit=None):
        if self.runtime.busy():raise ValueError('已有操作正在运行，请先暂停')
        mode=p.get('mode','continue');settings=self.run_settings(p.get('library_id'));source=None;force=None
        if mode not in ('continue','link','latest','all','verify','force','enrich'):raise ValueError('下载操作无效')
        if mode in ('latest','all'):
            if not self.settings.credentials.get('auth_token'):raise ValueError('请先保存或导入 X 登录凭证')
            source=likes_url(settings['username'])
        if scan_limit is not None and not Path(settings['output']).is_dir():
            raise ValueError('目标媒体库离线，请恢复后重试')
        if mode=='link':source=validate_source_url(p.get('url',''))
        if mode in ('force','enrich'):
            rows=self.store.get_many([int(p['job_id'])])
            if not rows:raise ValueError('找不到媒体记录')
            if mode=='force' and self.store.is_ignored(rows[0]['tweet_id']):raise ValueError('请先取消忽略')
            settings['output']=rows[0]['output'];force=rows[0]['id'] if mode=='force' else None
        label = '扫描推特点赞' if scan_limit is not None else '补全预览' if mode=='enrich' else '校验' if mode=='verify' else '下载'
        run_id=self.runtime.operation.begin(reading=bool(source) or mode=='enrich',label=label)

        def emit(event):
            self.runtime.emit({**event, 'run_id': run_id})
            if scan_limit is not None and event['type'] == 'finished':
                result = self.runtime.operation.snapshot()
                self.likes_result = {'state': 'ready' if result['phase'] == 'completed' else result['phase'],
                                     'found': result['discovered_media'], 'queued': result['queued'],
                                     'error': '' if result['phase'] == 'completed' else result['message']}
        self.runtime.runner=self.runner_factory(self.store,settings,dict(self.settings.credentials),emit)
        kwargs={'source':source,'limit':50 if mode=='latest' else 0,'recheck':mode=='verify','force_id':force}
        if automatic:
            kwargs['automatic'] = True
        if scan_limit is not None:
            kwargs.update(limit=scan_limit, scan_only=True)
        runner=self.runtime.runner
        target=(lambda:runner.enrich(rows[0])) if mode=='enrich' else (lambda:runner.run(**kwargs))
        self.runtime.worker=self.runtime.thread_factory(target=target,daemon=True)
        if scan_limit is not None:
            self.likes_result = None
            self.likes_worker = self.runtime.worker
        elif mode not in ('verify', 'enrich'):
            self.download_worker = self.runtime.worker
            if not automatic:
                self.set_manual_hold(False)
        self.runtime.worker.start()
        return True
