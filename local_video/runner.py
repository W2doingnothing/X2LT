# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Queue orchestration. Background workers never touch Tk widgets."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
from pathlib import Path
import re
import threading
import time

from .discovery import discover
from .archive import original_path, valid_receipt, save_receipt
from .auto_flatten import AutoFlatten
from .media import probe_video
from .images import extension_for, check_image, download_image
from .policy import validate_media_url, request_bytes_for
from .security import redact
from .transfer import Downloader, Paused, file_hash


class InstanceLock:
    def __init__(self, path):
        import msvcrt
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = path.open('a+b')
        if not path.stat().st_size:
            self.handle.write(b'0')
            self.handle.flush()
        self.handle.seek(0)
        try:
            msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.handle.close()
            raise RuntimeError('本地下载器已经在运行，请使用已打开的窗口') from None

    def close(self):
        self.handle.close()


def destination_for(row):
    if row.get('local_path'):
        path = Path(row['local_path'])
        if not path.is_absolute() or path.suffix.lower() != '.' + extension_for(row) or path.is_symlink():
            raise ValueError('整理后的文件路径无效')
        return path.resolve()
    return original_path(row)


class Runner:
    def __init__(self, store, settings, credentials, emit):
        self.store = store
        self.settings = dict(settings)
        self.credentials = dict(credentials)
        self.emit = emit
        self.cancel = threading.Event()
        self.ignore_baseline = self.store.ignore_snapshot()
        self._jobs_lock = threading.RLock()
        self._active_jobs = {}
        self._cancelled_jobs = set()

    def cancel_jobs(self, ids):
        with self._jobs_lock:
            self._cancelled_jobs.update(ids)
            for job in ids:
                if job in self._active_jobs: self._active_jobs[job].set()

    def active_jobs(self, ids):
        with self._jobs_lock:
            return bool(set(ids).intersection(self._active_jobs))

    def _message(self, text):
        self.emit({'type': 'message', 'text': redact(text, self.credentials.values())})

    def _place_download(self, row, cancel):
        manager = self.settings.get('_storage_guard')
        if not manager or not hasattr(manager, 'layout_for_output'):
            return
        library = manager.layout_for_output(row['output'])
        if not library or not library.get('auto_flatten'):
            return
        try:
            AutoFlatten(self.store).place_download(library, 'x', row['id'], cancel, self.emit)
        except Exception as exc:
            self._message(f'下载已完成，自动集中待重试：{exc}')

    def _job(self, row, recheck=False, force=False):
        stop = threading.Event()
        with self._jobs_lock:
            if row['id'] in self._cancelled_jobs: return 'skipped'
            self._active_jobs[row['id']] = stop
        parent = self.cancel
        class Cancellation:
            def is_set(self): return parent.is_set() or stop.is_set()
            def wait(self, seconds):
                deadline = time.monotonic() + seconds
                while not self.is_set():
                    left = deadline - time.monotonic()
                    if left <= 0: return False
                    stop.wait(min(.05,left))
                return True
        try:
            return self._perform_job(row, recheck, force, Cancellation())
        finally:
            with self._jobs_lock: self._active_jobs.pop(row['id'], None)

    def _perform_job(self, row, recheck=False, force=False, cancel=None):
        job_id = row['id']
        cancel = cancel or self.cancel
        if cancel.is_set() or self.store.get_many([job_id])[0]['state'] in ('removed','removing'):
            return 'skipped'
        if not recheck and not self.store.claim(job_id,force,self.ignore_baseline):
            self.emit({'type':'changed','id':job_id})
            return 'skipped'
        # Completed history survives missing/moved files and offline checks.
        if not force and not recheck:
            receipt = valid_receipt(row)
            if receipt:
                self.store.set_state(job_id, 'completed', **receipt)
                if receipt.get('local_path'):
                    self.store.relocate(job_id, receipt['local_path'], original_path(row))
                    self.store.finish_relocation(job_id)
                self._place_download(row, cancel)
                self.emit({'type': 'changed', 'id': job_id})
                return 'successful'
        self.emit({'type': 'changed', 'id': job_id})
        last = [time.monotonic(), 0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] >= .2 or total and done == total:
                self.emit({'type': 'progress', 'id': job_id, 'done': done, 'total': total, 'time': now})
                last[0] = now

        try:
            manager = self.settings.get('_storage_guard')
            library = manager.layout_for_output(row['output']) if hasattr(manager, 'layout_for_output') else None
            staging = not recheck and bool(library and library.get('auto_flatten'))
            destination = original_path(row) if force or staging else destination_for(row)
            if recheck:
                if not destination.is_file():
                    raise RuntimeError('找不到视频；下载记录保留，不会自动重新下载')
                if destination.stat().st_size != row['bytes'] or file_hash(destination, cancel, progress) != row['sha256']:
                    raise RuntimeError('视频校验不符；下载记录保留，可选中后强制重新下载')
                check_image(destination, extension_for(row)) if row.get('media_type') == 'image' else probe_video(destination)
                save_receipt(row, destination)
                self.store.set_state(job_id, 'completed', **{k: row[k] for k in ('bytes', 'sha256', 'verification')})
                return 'successful'
            downloader = Downloader(request_bytes=request_bytes_for(self.settings.get('request_strategy', 'conservative')), proxy=self.settings.get('proxy', ''), connections=self.settings.get('connections', 4), max_bytes=int(self.settings.get('max_mb', 0)) * 1024**2 or None, url_policy=lambda url: validate_media_url(url, row.get('media_type','video')))
            if self.settings.get('_storage_guard') is not None:
                downloader.storage_guard = self.settings['_storage_guard']
            transfer = (lambda url, path, *args, **kw: download_image(downloader, url, path, *args, **kw)) if row.get('media_type') == 'image' else downloader.download
            try:
                result = transfer(row['url'], destination, cancel, progress, **({'force': True} if force else {}))
            except Exception as exc:
                # Only refresh an expired URL, once; all other failures keep their evidence.
                if ('HTTP 403' not in str(exc) and 'HTTP 404' not in str(exc)) or cancel.is_set():
                    raise
                self._message('媒体地址失效，正在重新解析此推文')
                refreshed = next((v for v in discover(row['source'], self.credentials, self.settings.get('proxy', ''), cancel, quality=row['quality'], media_mode=row.get('media_type','video')) if v.media_id == row['media_id'] and v.media_type == row.get('media_type','video')), None)
                if not refreshed:
                    raise RuntimeError('无法重新解析对应视频') from None
                validate_media_url(refreshed.url, row.get("media_type","video"))
                self.store.update_url(job_id, refreshed.url)
                row = {**row, 'url': refreshed.url}
                result = transfer(refreshed.url, destination, cancel, progress, **({'force': True} if force else {}))
            if destination.exists():
                check_image(destination, extension_for(row)) if row.get('media_type') == 'image' else probe_video(destination)
            with self.store.lock:
                current = self.store.get_many([job_id])[0]
                if current['state'] in ('removed','removing'):
                    # A promoted file still needs verified ownership for pending cleanup.
                    self.store.removal.record_promoted(row,destination,result)
                    return 'skipped'
                self.store.set_state(job_id, 'completed', **{k: result[k] for k in ('bytes', 'sha256', 'verification')})
                save_receipt({**row, **result}, destination)
                if force or staging:
                    self.store.relocate(job_id, destination, destination)
                    self.store.finish_relocation(job_id)
                    save_receipt({**row, **result}, destination, clear_pending=True)
                if force:
                    self.store.set_force(job_id, False)
            self._place_download(row, cancel)
            if result.get('verification') == 'sha256-only':
                self._message('该文件缺少服务器总长度，仅完成本地 SHA256 记录，未确认传输长度')
            return 'successful'
        except Paused as exc:
            if self.store.get_many([job_id])[0]['state'] in ('removed','removing'): return 'skipped'
            message=redact(str(exc),self.credentials.values())
            self.store.set_state(job_id, 'completed' if recheck else 'paused', message, **({k: row[k] for k in ('bytes', 'sha256', 'verification')} if recheck else {}))
            if not self.cancel.is_set():self._message(message)
            self.emit({'type':'run_paused','text':message})
            return 'paused'
        except Exception as exc:
            message = redact(str(exc), self.credentials.values())
            self.store.set_state(job_id, 'completed' if recheck else 'failed', message, **({k: row[k] for k in ('bytes', 'sha256', 'verification')} if recheck else {}))
            self._message(message)
            return 'failed'
        finally:
            self.emit({'type': 'changed', 'id': job_id})

    def enrich(self, row):
        try:
            existing = self.store.tweet_rows(row['tweet_id'])
            count = 0
            for media in discover(row['source'], self.credentials, self.settings.get('proxy',''), self.cancel, media_mode='all'):
                for saved in existing:
                    if media.tweet_id == saved['tweet_id'] and media.media_id == saved['media_id'] and media.media_type == saved.get('media_type','video'):
                        self.store.update_metadata(saved['id'],media)
                        self.emit({'type':'changed','id':saved['id']})
                        count += 1
            self._message(f'预览元数据已更新 {count} 个已有任务；没有新增下载')
        except Paused:
            self._message('补全预览已暂停')
            self.emit({'type':'run_paused','text':'补全预览已暂停'})
        except Exception as exc:
            self._message(f'补全预览失败：{exc}')
            self.emit({'type':'run_failed','text':redact(str(exc),self.credentials.values())})
        finally:
            self.emit({'type':'finished'})

    def run(self, source=None, limit=0, recheck=False, force_id=None, *, automatic=False):
        self.ignore_baseline = self.store.ignore_snapshot()
        pending = deque()
        queued = set()
        scheduled = set()
        completed = set()
        outcomes = {}
        discovered_tweets = set()
        discovered_media = set()
        skipped_discovery = set()
        reading = bool(source)
        last_stats = None
        futures = set()
        future_jobs = {}

        def stats():
            nonlocal last_stats
            value={'type':'run_stats','reading':reading,'discovered_tweets':len(discovered_tweets),'discovered_media':len(discovered_media),
                'queued':len(queued),'processed':len(completed)+len(skipped_discovery),
                'successful':sum(v=='successful' for v in outcomes.values()),'failed':sum(v=='failed' for v in outcomes.values()),
                'skipped':len(skipped_discovery)+sum(v=='skipped' for v in outcomes.values()),'total':None if reading else len(queued)+len(skipped_discovery)}
            if value!=last_stats:self.emit(value);last_stats=dict(value)

        def collect_finished():
            finished={f for f in futures if f.done()}
            for future in finished:
                job_id=future_jobs.pop(future)
                try:outcomes[job_id]=future.result() or 'successful'
                except Exception as exc:
                    outcomes[job_id]='failed'
                    message=redact(str(exc),self.credentials.values())
                    self._message(message);self.emit({'type':'run_failed','text':message})
                completed.add(job_id)
            futures.difference_update(finished)

        def submit_available(pool):
            nonlocal futures
            collect_finished()
            while pending and len(futures) < 2 and not self.cancel.is_set():
                job_id = pending.popleft()
                rows = self.store.get_many([job_id])
                if not rows or job_id in scheduled:
                    continue
                row = rows[0]
                if recheck and row['state'] != 'completed':
                    continue
                if not (force_id is not None or row.get('force_download') or row['state'] in {'pending','paused','failed'} or recheck):
                    continue
                scheduled.add(job_id)
                future = pool.submit(self._job, row, recheck=recheck and row['state']=='completed', force=not recheck and (force_id is not None or bool(row.get('force_download'))))
                futures.add(future)
                future_jobs[future] = job_id
            self.emit({'type': 'operation_progress', 'done': len(completed), 'total': len(scheduled), 'text': '校验' if recheck else '下载'})
            stats()

        try:
            pending.extend(self.store.runnable_ids(self.settings.get('output'), recheck, force_id, automatic=automatic))
            queued.update(pending)
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='video-file') as pool:
                submit_available(pool)
                if source:
                    self._message('正在读取视频列表；解析和下载使用同一代理')
                    start = time.monotonic()
                    for video in discover(source, self.credentials, self.settings.get('proxy', ''), self.cancel, limit=limit, quality=self.settings.get('quality', 'best'), media_mode=self.settings.get('media_mode','video')):
                        key=(video.tweet_id,video.media_id,video.media_type)
                        if key in discovered_media:continue
                        discovered_media.add(key);discovered_tweets.add(video.tweet_id)
                        job_id = self.store.enqueue(video, self.settings['output'], self.settings.get('quality', 'best'), baseline=self.ignore_baseline)
                        if job_id is None:
                            skipped_discovery.add(('ignored',key));stats()
                            continue
                        if job_id not in queued:
                            row=self.store.get_many([job_id])[0]
                            if row['state']=='completed' and not row.get('force_download'):
                                skipped_discovery.add(job_id)
                            else:
                                pending.append(job_id)
                                queued.add(job_id)
                        self.emit({'type': 'changed', 'id': job_id})
                        submit_available(pool)
                    self._message(f'读取列表耗时 {time.monotonic() - start:.1f} 秒')
                    self.emit({'type': 'discovery_finished'})
                    reading=False;stats()
                while not self.cancel.is_set():
                    submit_available(pool)
                    if not futures:
                        break
                    wait(futures, timeout=.2, return_when=FIRST_COMPLETED)
        except Paused:
            self._message('已暂停；下次点继续未完成即可续传')
            self.emit({'type':'run_paused','text':'已暂停；下次点继续未完成即可续传'})
        except Exception as exc:
            self._message(f'任务中断：{exc}')
            self.emit({'type':'run_failed','text':redact(str(exc),self.credentials.values())})
        finally:
            collect_finished()
            stats()
            if self.cancel.is_set():self.emit({'type':'run_paused','text':'已暂停；已完成的媒体和分段保留'})
            self.emit({'type': 'finished'})
