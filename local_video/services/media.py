# SPDX-License-Identifier: GPL-2.0-only
"""Bounded preview work and native media commands."""
import base64
from io import BytesIO
import threading
from ..policy import profile_url, validate_source_url
from ..runner import destination_for
from ..emoji import validate_emoji


class MediaService:
    def __init__(self, store, settings, libraries, runtime, native, cache, assets):
        self.store,self.settings,self.libraries,self.runtime=store,settings,libraries,runtime
        self.native,self.cache,self.assets=native,cache,assets
        self.preview_lock=threading.BoundedSemaphore(2)

    def set_cover(self,p):
        library=self.libraries.manager.get(p['id']);kind=p.get('kind')
        emoji=validate_emoji(p['emoji']) if 'emoji' in p else None
        if kind=='identity':return self.libraries.manager.appearance(library['id'],emoji=validate_emoji(p.get('value')))
        if kind=='image':
            selected=self.native.choose_image()
            if not selected:return None
            cover=self.assets.import_cover(selected)
        elif kind=='emoji':
            value=validate_emoji(p.get('value',''));emoji=value;cover={'kind':'emoji','value':value}
        elif kind=='reset':cover=None
        else:raise ValueError('封面类型无效')
        self.libraries.manager.appearance(library['id'],emoji=emoji,cover=cover)
        return cover

    def open(self,action,p):
        if action=='open_profile':return self.native.open_url(profile_url(p['handle']))
        rows=self.store.get_many([p['job_id']])
        if not rows:raise ValueError('找不到媒体记录')
        if action=='open_tweet':return self.native.open_url(validate_source_url(rows[0]['source']))
        path=destination_for(rows[0])
        return self.native.open_file(path.parent if action=='open_folder' else path)

    def clear(self,p):
        if self.runtime.busy():raise ValueError('请暂停后清理缓存')
        self.cache.trim(clear=True);return True

    def asset(self,action,p):
        if not self.preview_lock.acquire(blocking=False):return None
        try:
            if action=='cover':return self.assets.cover(self.libraries.manager.get(p['id']).get('cover'))
            author=self.store.catalog.author(p['id'])
            if not author.get('avatar_url'):return None
            return self.assets.avatar(author['avatar_url'],self.settings.credentials.get('proxy',''),self.runtime.cancel)
        except Exception:return None
        finally:self.preview_lock.release()


    def preview(self,p):
        if not self.preview_lock.acquire(blocking=False):return None
        try:
            rows=self.store.get_many([int(p['job_id'])])
            if not rows:return None
            image=self.cache.load(rows[0],self.settings.credentials.get('proxy',''),self.runtime.cancel,(900,600))
            try:
                buffer=BytesIO();image.save(buffer,format='JPEG',quality=85)
                return 'data:image/jpeg;base64,'+base64.b64encode(buffer.getvalue()).decode('ascii')
            finally:image.close()
        except Exception:return None
        finally:self.preview_lock.release()
