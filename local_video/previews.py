# SPDX-License-Identifier: GPL-2.0-only
"""Bounded raster cache. Workers return PIL images, never Tk objects."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import threading
import tempfile
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit

import requests

from .images import read_image, is_alias
from .policy import normalize_proxy, validate_image_url
from .transfer import Paused

MAX_RESPONSE = 5 * 1024**2


def thumbnail_url(url):
    validate_image_url(url)
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query['name'] = 'small'
    return parts._replace(query=urlencode(query), fragment='').geturl()


class PreviewCache:
    def __init__(self, root, max_bytes=500 * 1024**2, url_policy=validate_image_url):
        self.root = Path(root)
        if is_alias(self.root):
            raise ValueError('预览缓存不能为符号链接')
        self.root.mkdir(parents=True, exist_ok=True)
        info = self.root.stat()
        self.root_identity = (info.st_dev, info.st_ino)
        self.max_bytes = max_bytes
        self.url_policy = url_policy
        self.lock = threading.RLock()

    def _check_root(self):
        if is_alias(self.root):
            raise ValueError('缓存目录变成了目录联接或符号链接')
        info = self.root.stat()
        if (info.st_dev, info.st_ino) != self.root_identity:
            raise ValueError('缓存目录已被替换，请重启程序')

    def _fetch(self, url, proxy, cancel):
        proxy = normalize_proxy(proxy)
        with requests.Session() as session:
            session.trust_env = False
            if proxy:
                session.proxies = {'http': proxy, 'https': proxy}
            start = time.monotonic()
            for _ in range(4):
                if cancel.is_set():
                    raise Paused('预览已取消')
                self.url_policy(url)
                with session.get(url, stream=True, timeout=(5, 8), allow_redirects=False, headers={'Accept-Encoding': 'identity'}) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers['Location'])
                        self.url_policy(url)
                        continue
                    response.raise_for_status()
                    if int(response.headers.get('Content-Length', '0')) > MAX_RESPONSE:
                        raise ValueError('预览文件过大')
                    body = bytearray()
                    for chunk in response.iter_content(64 * 1024):
                        if cancel.is_set():
                            raise Paused('预览已取消')
                        if time.monotonic() - start > 20:
                            raise ValueError('预览请求超时')
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE:
                            raise ValueError('预览文件过大')
                    return bytes(body)
            raise ValueError('预览重定向过多')

    def load(self, row, proxy, cancel, size):
        if cancel.is_set():
            raise Paused('预览已取消')
        if row.get('media_type') == 'image' and row.get('state') == 'completed':
            from .runner import destination_for
            local = destination_for(row)
            if local.is_file() and not local.is_symlink():
                return read_image(local, size=size)
        url = thumbnail_url(row.get('preview_url', ''))
        key = hashlib.sha256(url.encode()).hexdigest()
        return self.load_raster(key, lambda: url, proxy, cancel, size)

    def load_raster(self, key, resolve_url, proxy, cancel, size):
        def generate():
            return read_image(BytesIO(self._fetch(resolve_url(), proxy, cancel)), size=(1280, 1280))

        return self.load_generated(key, generate, cancel, size)

    def load_generated(self, key, generate, cancel, size):
        if not re.fullmatch(r'[0-9a-f]{64}', key):
            raise ValueError('预览缓存编号无效')
        if cancel.is_set():
            raise Paused('预览已取消')
        path = self.root / (key + '.jpg')
        with self.lock:
            self._check_root()
            if is_alias(path):
                raise ValueError('预览文件不能为符号链接')
            if path.is_file():
                try:
                    image = read_image(path, size=size)
                    os.utime(path, None)
                    return image
                except ValueError:
                    path.unlink(missing_ok=True)
        image = generate()
        if image is None:
            return None
        try:
            if cancel.is_set():
                raise Paused('预览已取消')
            # No active writer outside this lock, so trim/clear cannot remove it.
            with self.lock:
                self._check_root()
                if is_alias(path):
                    raise ValueError('预览文件不能为符号链接')
                fd, temp_name = tempfile.mkstemp(prefix=key+'.',suffix='.tmp',dir=self.root)
                temp = Path(temp_name)
                try:
                    with os.fdopen(fd,'wb') as output:
                        image.save(output, format='JPEG', quality=85)
                    self._check_root()
                    os.replace(temp, path)
                finally:
                    self._check_root()
                    temp.unlink(missing_ok=True)
                self.trim()
            image.thumbnail(size)
            return image
        except Exception:
            image.close()
            raise

    def trim(self, clear=False):
        with self.lock:
            self._check_root()
            for temp in self.root.iterdir():
                if re.fullmatch(r'[0-9a-f]{64}\.[A-Za-z0-9_-]+\.tmp',temp.name) and not is_alias(temp) and temp.is_file():
                    self._check_root()
                    temp.unlink(missing_ok=True)
            files = [p for p in self.root.iterdir() if re.fullmatch(r'[0-9a-f]{64}\.jpg', p.name) and not is_alias(p) and p.is_file()]
            files.sort(key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            for path in files:
                if not clear and total <= self.max_bytes:
                    break
                total -= path.stat().st_size
                self._check_root()
                path.unlink(missing_ok=True)


class PreviewService:
    def __init__(self, root, emit):
        self.cache = PreviewCache(root)
        self.emit = emit
        self.cancel = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='preview')
        self.slots = threading.BoundedSemaphore(8)

    def request(self, row, proxy, token, size):
        if self.cancel.is_set() or not self.slots.acquire(blocking=False):
            return None

        def work():
            image = None
            try:
                image = self.cache.load(row, proxy, self.cancel, size)
                if not self.cancel.is_set():
                    self.emit({'type': 'preview', 'token': token, 'image': image})
                    image = None
            except Exception:
                if not self.cancel.is_set():
                    self.emit({'type': 'preview', 'token': token, 'image': None})
            finally:
                if image is not None:
                    image.close()
                self.slots.release()
        try:
            return self.pool.submit(work)
        except RuntimeError:
            self.slots.release()
            return None

    def clear(self):
        self.cache.trim(clear=True)

    def close(self):
        self.cancel.set()
        self.pool.shutdown(wait=False, cancel_futures=True)
