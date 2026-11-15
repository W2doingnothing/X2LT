# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Queue orchestration. Background workers never touch Tk widgets."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import os
from pathlib import Path
import re
import threading
import time

from .discovery import discover
from .archive import original_path, valid_receipt, save_receipt
from .media import probe_video
from .policy import validate_media_url
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
        if not path.is_absolute() or path.suffix.lower() != '.mp4' or path.is_symlink():
            raise ValueError('整理后的文件路径无效')
        return path.resolve()
    for key in ('author', 'tweet_id', 'media_id', 'quality'):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', str(row[key])):
            raise ValueError('任务文件名含有不安全字符')
    root = Path(row['output']).resolve()
    path = root / row['quality'] / row['author'] / row['tweet_id'] / f"{row['tweet_id']}_{row['media_id']}.mp4"
    if not path.resolve().is_relative_to(root):
        raise ValueError('目标路径超出保存目录')
    return path


class Runner:
    def __init__(self, store, settings, credentials, emit):
        self.store = store
        self.settings = dict(settings)
        self.credentials = dict(credentials)
        self.emit = emit
        self.cancel = threading.Event()

    def _message(self, text):
        self.emit({'type': 'message', 'text': redact(text, self.credentials.values())})

    def _job(self, row, recheck=False, force=False):
        job_id = row['id']
        if force:
            self.store.set_force(job_id, True)
        # Completed history survives missing/moved files and offline checks.
        if not force and not recheck:
            receipt = valid_receipt(row)
            if receipt:
                self.store.set_state(job_id, 'completed', **receipt)
                if receipt.get('local_path'):
                    self.store.relocate(job_id, receipt['local_path'], original_path(row))
                    self.store.finish_relocation(job_id)
                self.emit({'type': 'changed', 'id': job_id})
                return
        if not recheck:
            self.store.set_state(job_id, 'downloading')
        self.emit({'type': 'changed', 'id': job_id})
        last = [time.monotonic(), 0]

        def progress(done, total):
            now = time.monotonic()
            if now - last[0] >= .2 or total and done == total:
                self.emit({'type': 'progress', 'id': job_id, 'done': done, 'total': total, 'time': now})
                last[0] = now

        try:
            destination = original_path(row) if force else destination_for(row)
            if recheck:
                if not destination.is_file():
                    raise RuntimeError('找不到视频；下载记录保留，不会自动重新下载')
                if destination.stat().st_size != row['bytes'] or file_hash(destination, self.cancel, progress) != row['sha256']:
                    raise RuntimeError('视频校验不符；下载记录保留，可选中后强制重新下载')
                probe_video(destination)
                save_receipt(row, destination)
                self.store.set_state(job_id, 'completed', **{k: row[k] for k in ('bytes', 'sha256', 'verification')})
                return
            downloader = Downloader(proxy=self.settings.get('proxy', ''), connections=self.settings.get('connections', 4), max_bytes=int(self.settings.get('max_mb', 4096)) * 1024**2)
            try:
                result = downloader.download(row['url'], destination, self.cancel, progress, **({'force': True} if force else {}))
            except Exception as exc:
                # Only refresh an expired URL, once; all other failures keep their evidence.
                if ('HTTP 403' not in str(exc) and 'HTTP 404' not in str(exc)) or self.cancel.is_set():
                    raise
                self._message('媒体地址失效，正在重新解析此推文')
                refreshed = next((v for v in discover(row['source'], self.credentials, self.settings.get('proxy', ''), self.cancel, quality=row['quality']) if v.media_id == row['media_id']), None)
                if not refreshed:
                    raise RuntimeError('无法重新解析对应视频') from None
                validate_media_url(refreshed.url)
                self.store.update_url(job_id, refreshed.url)
                row = {**row, 'url': refreshed.url}
                result = downloader.download(refreshed.url, destination, self.cancel, progress, **({'force': True} if force else {}))
            if destination.exists():
                probe_video(destination)
            self.store.set_state(job_id, 'completed', **{k: result[k] for k in ('bytes', 'sha256', 'verification')})
            save_receipt({**row, **result}, destination)
            if force:
                self.store.relocate(job_id, destination, destination)
                self.store.finish_relocation(job_id)
                save_receipt({**row, **result}, destination, clear_pending=True)
                self.store.set_force(job_id, False)
            if result.get('verification') == 'sha256-only':
                self._message('该文件缺少服务器总长度，仅完成本地 SHA256 记录，未确认传输长度')
        except Paused:
            self.store.set_state(job_id, 'completed' if recheck else 'paused', **({k: row[k] for k in ('bytes', 'sha256', 'verification')} if recheck else {}))
        except Exception as exc:
            message = redact(str(exc), self.credentials.values())
            self.store.set_state(job_id, 'completed' if recheck else 'failed', message, **({k: row[k] for k in ('bytes', 'sha256', 'verification')} if recheck else {}))
            self._message(message)
        finally:
            self.emit({'type': 'changed', 'id': job_id})

    def run(self, source=None, limit=0, recheck=False, force_id=None):
        scheduled = set()
        completed = set()
        futures = set()
        future_jobs = {}

        def submit_available(pool):
            nonlocal futures
            finished = {f for f in futures if f.done()}
            for future in finished:
                future.result()
                completed.add(future_jobs.pop(future))
            futures -= finished
            for row in reversed(self.store.list()):
                if len(futures) >= 2 or self.cancel.is_set():
                    break
                if force_id is not None and row['id'] != force_id:
                    continue
                if recheck and row['state'] != 'completed':
                    continue
                if row['id'] not in scheduled and (force_id is not None or row.get('force_download') or row['state'] in {'pending', 'paused', 'failed'} or recheck and row['state'] == 'completed'):
                    if force_id is None and self.settings.get('output') and str(Path(self.settings['output']).resolve()) != row['output']:
                        continue
                    scheduled.add(row['id'])
                    future = pool.submit(self._job, row, recheck=recheck and row['state'] == 'completed', force=not recheck and (force_id is not None or bool(row.get('force_download'))))
                    futures.add(future)
                    future_jobs[future] = row['id']
            self.emit({'type': 'operation_progress', 'done': len(completed), 'total': len(scheduled), 'text': '校验' if recheck else '下载'})

        try:
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix='video-file') as pool:
                submit_available(pool)
                if source:
                    self._message('正在读取视频列表；解析和下载使用同一代理')
                    start = time.monotonic()
                    for video in discover(source, self.credentials, self.settings.get('proxy', ''), self.cancel, limit=limit, quality=self.settings.get('quality', 'best')):
                        self.store.enqueue(video, self.settings['output'], self.settings.get('quality', 'best'))
                        self.emit({'type': 'changed'})
                        submit_available(pool)
                    self._message(f'读取列表耗时 {time.monotonic() - start:.1f} 秒')
                    self.emit({'type': 'discovery_finished'})
                while not self.cancel.is_set():
                    submit_available(pool)
                    if not futures:
                        break
                    wait(futures, timeout=.2, return_when=FIRST_COMPLETED)
        except Paused:
            self._message('已暂停；下次点继续未完成即可续传')
        except Exception as exc:
            self._message(f'任务中断：{exc}')
        finally:
            self.emit({'type': 'finished'})
