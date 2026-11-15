# SPDX-License-Identifier: GPL-2.0-only
"""Bounded native raster assets; no arbitrary file paths exposed by the bridge."""
import base64
import hashlib
from io import BytesIO
import os
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import urlsplit
import uuid

from .images import is_alias,read_image
from .previews import PreviewCache


def validate_avatar_url(url):
    p=urlsplit(url)
    try:valid=p.scheme=='https' and p.hostname=='pbs.twimg.com' and p.port in (None,443) and not p.username and not p.password and p.path.startswith('/profile_images/') and not p.fragment
    except ValueError:valid=False
    if not valid:raise ValueError('头像仅允许官方头像 CDN')
    return url


def _data_url(image):
    buf=BytesIO();image.save(buf,format='JPEG',quality=85)
    return 'data:image/jpeg;base64,'+base64.b64encode(buf.getvalue()).decode('ascii')


class LibraryAssets:
    def __init__(self,root,cache):
        self.root=Path(root)
        if any(is_alias(p) for p in (self.root,*self.root.parents)):raise ValueError('封面目录不能为链接')
        self.root.mkdir(parents=True,exist_ok=True)
        s=self.root.stat();self.identity=(s.st_dev,s.st_ino)
        self.cache=cache
        self.fetcher=PreviewCache(cache.root,max_bytes=cache.max_bytes,url_policy=validate_avatar_url)
        self.fetcher.lock=cache.lock

    def _root_check(self):
        if is_alias(self.root):raise ValueError('封面目录被替换')
        s=self.root.stat()
        if (s.st_dev,s.st_ino)!=self.identity:raise ValueError('封面目录被替换，请重启程序')

    def import_cover(self,path):
        self._root_check();source=Path(path)
        if source.suffix.lower() not in ('.jpg','.jpeg','.png','.webp') or any(is_alias(p) for p in (source,*source.parents)):raise ValueError('请选择本地 JPEG、PNG 或 WebP 图片')
        if not source.is_file() or source.stat().st_size>16*1024**2:raise ValueError('封面图片不能超过 16 MiB')
        with source.open('rb') as stream:
            content=stream.read(16*1024**2+1)
        if len(content)>16*1024**2:raise ValueError('封面图片过大')
        image=read_image(BytesIO(content),size=(1600,900));key=uuid.uuid4().hex
        try:
            self._root_check();image.save(self.root/(key+'.jpg'),format='JPEG',quality=85)
        finally:image.close()
        return {'kind':'image','value':key}

    def cover(self,cover):
        self._root_check()
        if not isinstance(cover,dict) or cover.get('kind')!='image':return None
        key=cover.get('value','')
        if not isinstance(key,str) or not re.fullmatch('[0-9a-f]{32}',key):raise ValueError('封面资源标识无效')
        path=self.root/(key+'.jpg')
        if is_alias(path):raise ValueError('封面文件不能为链接')
        if not path.exists():return None
        image=read_image(path,size=(900,600))
        try:return _data_url(image)
        finally:image.close()

    def avatar(self,url,proxy,cancel):
        validate_avatar_url(url)
        key=hashlib.sha256(('avatar:'+url).encode()).hexdigest();path=self.cache.root/(key+'.jpg')
        with self.cache.lock:
            self.cache._check_root()
            if is_alias(path):raise ValueError('头像缓存不能为链接')
            if path.is_file() and time.time()-path.stat().st_mtime<30*86400:
                try:
                    image=read_image(path,size=(128,128))
                    try:return _data_url(image)
                    finally:image.close()
                except ValueError:path.unlink(missing_ok=True)
        try:body=self.fetcher._fetch(url,proxy,cancel)
        except Exception:
            with self.cache.lock:
                if path.is_file() and not is_alias(path):
                    image=read_image(path,size=(128,128))
                    try:return _data_url(image)
                    finally:image.close()
            return None
        image=read_image(BytesIO(body),size=(128,128))
        try:
            with self.cache.lock:
                self.cache._check_root()
                if is_alias(path):raise ValueError('头像缓存不能为链接')
                fd,name=tempfile.mkstemp(prefix=key+'.',suffix='.tmp',dir=self.cache.root)
                try:
                    with os.fdopen(fd,'wb') as stream:image.save(stream,format='JPEG',quality=85)
                    os.replace(name,path);self.cache.trim()
                finally:Path(name).unlink(missing_ok=True)
            return _data_url(image)
        finally:image.close()
