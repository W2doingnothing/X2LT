# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Bounded range downloads with durable checkpoints and verified promotion."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
from email.utils import parsedate_to_datetime
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from urllib.parse import urljoin, urlsplit

import requests

from .policy import normalize_proxy, validate_media_url
from .security import atomic_json


class TransferError(RuntimeError):
    pass


class Paused(TransferError):
    pass


class _RangeIgnored(TransferError):
    pass


def _strong_validator(headers):
    etag = headers.get('ETag', '')
    if re.fullmatch(r'"[^"\r\n]*"', etag):
        return etag, 'ETag'
    modified = headers.get('Last-Modified', '')
    # RFC 9110 section 8.8.2.2: a client may use a date as strong only
    # when the origin's Date is at least 60 seconds after Last-Modified.
    try:
        if (parsedate_to_datetime(headers.get('Date', '')) - parsedate_to_datetime(modified)).total_seconds() >= 60:
            return modified, 'Last-Modified'
    except (ValueError, TypeError, OverflowError):
        pass
    return '', ''


def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf8'))
    except (ValueError, OSError):
        return {}


def file_hash(path, cancel=None, progress=None):
    digest = hashlib.sha256()
    done = 0
    total = Path(path).stat().st_size
    with Path(path).open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            if cancel is not None and cancel.is_set():
                raise Paused('已暂停，进度已保留')
            digest.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    return digest.hexdigest()


class Downloader:
    def __init__(self, proxy='', connections=4, max_bytes=4 * 1024**3,
                 retries=3, chunk_size=256 * 1024, url_policy=validate_media_url,
                 request_bytes=1024 * 1024, storage_guard=None):
        self.proxy = normalize_proxy(proxy)
        self.connections = max(1, min(4, int(connections)))
        self.max_bytes = max_bytes
        self.retries = max(1, retries)
        self.chunk_size = chunk_size
        self.url_policy = url_policy
        self.request_bytes = max(chunk_size, int(request_bytes))
        self.storage_guard = storage_guard

    def _session(self):
        session = requests.Session()
        session.trust_env = False
        session.headers.update({'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36', 'Accept-Encoding': 'identity', 'Referer': 'https://x.com/'})
        if self.proxy:
            session.proxies = {'http': self.proxy, 'https': self.proxy}
        return session

    def _request(self, session, url, **kwargs):
        for _ in range(6):
            self.url_policy(url)
            response = session.get(url, stream=True, timeout=(15, 20), allow_redirects=False, **kwargs)
            if response.status_code not in {301, 302, 303, 307, 308}:
                return response
            location = response.headers.get('Location')
            response.close()
            if not location:
                raise TransferError('重定向缺少目标地址')
            url = urljoin(url, location)
        raise TransferError('重定向次数过多')

    def _probe(self, url, cancel):
        for attempt in range(self.retries):
            if cancel.is_set():
                raise Paused('已暂停')
            try:
                with self._session() as session, self._request(session, url, headers={'Range': 'bytes=0-0'}) as response:
                    if response.status_code in {429, 500, 502, 503, 504}:
                        raise requests.HTTPError('服务器暂时不可用')
                    if response.status_code == 206:
                        match = re.fullmatch(r'bytes 0-0/(\d+)', response.headers.get('Content-Range', ''))
                        if not match:
                            raise TransferError('服务器返回错误的 Content-Range')
                        total = int(match[1])
                        ranged = True
                    elif response.status_code == 200:
                        total = int(response.headers.get('Content-Length', 0))
                        ranged = False
                    else:
                        raise TransferError(f'视频探测失败：HTTP {response.status_code}；403/404 可重新解析链接')
                    if response.headers.get('Content-Encoding', 'identity') != 'identity':
                        raise TransferError('服务器返回压缩传输，不能安全续传')
                    validator, header = _strong_validator(response.headers)
                    return {'total': total, 'validator': validator, 'validator_header': header, 'ranged': ranged}
            except requests.RequestException as exc:
                if attempt + 1 >= self.retries:
                    raise TransferError('视频连接失败（检查代理和网络）') from exc
                if cancel.wait(min(2**attempt, 5)):
                    raise Paused('已暂停')

    def download(self, url, destination, cancel=None, progress=None, _force_single=False, force=False):
        if self.storage_guard is not None:
            with self.storage_guard.lease(destination) as capacity:
                return self._download(url,destination,cancel,progress,_force_single,force,capacity)
        return self._download(url,destination,cancel,progress,_force_single,force)

    def _download(self, url, destination, cancel=None, progress=None, _force_single=False, force=False, capacity=None):
        self.url_policy(url)
        cancel = cancel if cancel is not None else threading.Event()
        progress = progress or (lambda done, total: None)
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.is_symlink():
            raise TransferError('目标文件不能是符号链接')
        identity = urlsplit(url)._replace(query='', fragment='').geturl()
        complete_path = destination.with_suffix(destination.suffix + '.verified.json')
        complete = _json(complete_path)
        if destination.exists() and destination.stat().st_size > self.max_bytes:
            raise TransferError('已有文件超出大小限制')
        if not force and destination.exists() and complete.get('source') == identity and complete.get('bytes') == destination.stat().st_size:
            if complete.get('sha256') == file_hash(destination, cancel):
                progress(complete['bytes'], complete['bytes'])
                return {**complete, 'reused': True}
        info = self._probe(url, cancel)
        total = info['total']
        if total > self.max_bytes:
            raise TransferError('视频超出所设最大文件大小')
        if total < 0:
            raise TransferError('无效的文件大小')
        needed = total * 2 + 16 * 1024 * 1024 if total else 64 * 1024 * 1024
        if capacity is not None:
            capacity.reserve((total or self.max_bytes)*2 + 16*1024*1024)
        if shutil.disk_usage(destination.parent).free < needed:
            raise TransferError('磁盘剩余空间不足（分段合并需要额外空间）')
        ranged = bool(not _force_single and info['ranged'] and total > 0 and info['validator'])
        count = min(self.connections, max(1, total // (256 * 1024))) if ranged else 1
        ranges = [[total * i // count, total * (i + 1) // count - 1] for i in range(count)] if total else [[0, None]]
        parts_dir = destination.with_suffix(destination.suffix + '.parts')
        if parts_dir.is_symlink():
            raise TransferError('临时目录不能是符号链接')
        parts_dir.mkdir(exist_ok=True)
        manifest_path = parts_dir / 'manifest.json'
        expected = {'source': identity, 'total': total, 'validator': info['validator'], 'ranges': ranges, 'ranged': ranged}
        previous = _json(manifest_path)
        if previous != expected or not ranged:
            for child in parts_dir.iterdir():
                if re.fullmatch(r'\d+\.part(?:\.json)?', child.name) and not child.is_dir():
                    child.unlink()
        atomic_json(manifest_path, expected)
        done = [0] * count
        lock = threading.Lock()
        abort = threading.Event()

        def report(index, size):
            with lock:
                done[index] = size
                progress(sum(done), total)

        def segment(index):
            start, end = ranges[index]
            path = parts_dir / f'{index}.part'
            checkpoint = parts_dir / f'{index}.part.json'
            if path.is_symlink() or checkpoint.is_symlink():
                raise TransferError('分段文件不能是符号链接')
            state = _json(checkpoint)
            offset = state.get('bytes', 0) if ranged else 0
            segment_size = end - start + 1 if end is not None else None
            if not isinstance(offset, int) or offset < 0 or (segment_size is not None and offset > segment_size):
                offset = 0
            if offset and path.exists() and path.stat().st_size >= offset:
                digest = hashlib.sha256()
                with path.open('rb') as handle:
                    remaining = offset
                    while remaining:
                        block = handle.read(min(1024 * 1024, remaining))
                        digest.update(block)
                        remaining -= len(block)
                if digest.hexdigest() != state.get('sha256'):
                    offset = 0
            else:
                offset = 0
            if not offset:
                digest = hashlib.sha256()
            with path.open('r+b' if path.exists() else 'w+b') as handle:
                handle.truncate(offset)
                handle.seek(offset)
                report(index, offset)

                def save():
                    handle.flush()
                    os.fsync(handle.fileno())
                    atomic_json(checkpoint, {'bytes': offset, 'sha256': digest.hexdigest()})

                checkpoint_at = offset
                checkpoint_time = time.monotonic()
                try:
                    if segment_size is not None and offset == segment_size:
                        return
                    # Reuse each worker's connection; bounded requests limit lost progress
                    # when the proxy resets a long response.
                    with self._session() as session:
                        while segment_size is None or offset < segment_size:
                            request_end = min(end, start + offset + self.request_bytes - 1) if ranged else end
                            for attempt in range(self.retries):
                                if cancel.is_set() or abort.is_set():
                                    raise Paused('已暂停，下载分段已保留')
                                headers = {'Range': f'bytes={start + offset}-{request_end}', 'If-Range': info['validator']} if ranged else {}
                                try:
                                    with self._request(session, url, headers=headers) as response:
                                        if response.status_code in {429, 500, 502, 503, 504}:
                                            raise requests.HTTPError('服务器暂时不可用')
                                        if response.status_code in {403, 404}:
                                            raise TransferError(f'视频地址失效：HTTP {response.status_code}')
                                        if ranged:
                                            if response.status_code == 200:
                                                raise _RangeIgnored('服务器忽略分段，安全切换为单流')
                                            wanted = f'bytes {start + offset}-{request_end}/{total}'
                                            if response.status_code != 206 or response.headers.get('Content-Range') != wanted:
                                                raise TransferError('续传范围不一致或资源已变化，请重新开始任务')
                                            if response.headers.get(info['validator_header'], '') != info['validator']:
                                                raise TransferError('下载过程中资源标识变化，拒绝拼接')
                                        elif response.status_code != 200:
                                            raise TransferError(f'下载失败：HTTP {response.status_code}')
                                        if response.headers.get('Content-Encoding', 'identity') != 'identity':
                                            raise TransferError('不支持压缩的视频传输')
                                        if not ranged and total:
                                            declared = response.headers.get('Content-Length')
                                            if declared is not None and int(declared) != total:
                                                raise TransferError('视频大小发生变化，请重试')
                                        for chunk in response.iter_content(chunk_size=self.chunk_size):
                                            if cancel.is_set() or abort.is_set():
                                                raise Paused('已暂停，下载分段已保留')
                                            if not chunk:
                                                continue
                                            if (request_end is not None and start + offset + len(chunk) > request_end + 1) or start + offset + len(chunk) > self.max_bytes:
                                                raise TransferError('实际数据超出预期范围或大小限制')
                                            handle.write(chunk)
                                            digest.update(chunk)
                                            offset += len(chunk)
                                            report(index, offset)
                                            if offset - checkpoint_at >= 1024 * 1024 or time.monotonic() - checkpoint_time >= 1:
                                                save()
                                                checkpoint_at = offset
                                                checkpoint_time = time.monotonic()
                                        if request_end is not None and start + offset != request_end + 1:
                                            raise TransferError('视频长度不足，未标记完成')
                                        if offset == 0:
                                            raise TransferError('视频为空')
                                        if not ranged:
                                            return
                                        break
                                except requests.RequestException as exc:
                                    if attempt + 1 >= self.retries:
                                        raise TransferError('视频连接中断；已保留可续传分段') from exc
                                    if not ranged:
                                        offset = 0
                                        digest = hashlib.sha256()
                                        handle.seek(0)
                                        handle.truncate()
                                    if cancel.wait(min(2**attempt, 5)):
                                        raise Paused('已暂停')
                finally:
                    save()

        with ThreadPoolExecutor(max_workers=count, thread_name_prefix='video-range') as pool:
            futures = [pool.submit(segment, i) for i in range(count)]
            error = None
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    if error is None or isinstance(error, Paused) and not isinstance(exc, Paused):
                        error = exc
                    abort.set()
            if error:
                if isinstance(error, _RangeIgnored) and not cancel.is_set():
                    return self._download(url, destination, cancel, progress, _force_single=True, force=force, capacity=capacity)
                raise error
        if cancel.is_set():
            raise Paused('已暂停，进度已保留')
        joining = destination.with_suffix(destination.suffix + '.joining')
        digest = hashlib.sha256()
        actual = 0
        try:
            with joining.open('wb') as output:
                for i in range(count):
                    with (parts_dir / f'{i}.part').open('rb') as handle:
                        while block := handle.read(1024 * 1024):
                            if cancel.is_set():
                                raise Paused('已暂停，分段保留')
                            output.write(block)
                            digest.update(block)
                            actual += len(block)
                output.flush()
                os.fsync(output.fileno())
            if actual == 0 or total and actual != total or actual > self.max_bytes:
                raise TransferError('合并后的长度校验失败')
            result = {'source': identity, 'bytes': actual, 'sha256': digest.hexdigest(), 'verification': 'length+sha256' if total else 'sha256-only', 'reused': False}
            os.replace(joining, destination)
            atomic_json(complete_path, result)
            for i in range(count):
                (parts_dir / f'{i}.part').unlink(missing_ok=True)
                (parts_dir / f'{i}.part.json').unlink(missing_ok=True)
            manifest_path.unlink(missing_ok=True)
            parts_dir.rmdir()
            progress(actual, total or actual)
            return result
        finally:
            joining.unlink(missing_ok=True)
