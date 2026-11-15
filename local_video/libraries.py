# SPDX-License-Identifier: GPL-2.0-only
"""Persistent independent library quotas with process-wide peak-space leases."""
import json
import os
from pathlib import Path
import shutil
import threading
import time
import uuid

from .images import is_alias
from .security import atomic_json
from .transfer import Paused
from .archive import comparable_path


def _path(value):
    raw=Path(value).expanduser().absolute()
    if any(is_alias(p) for p in (raw,*raw.parents)):
        raise ValueError('媒体库及其上级路径不能为符号链接或目录联接')
    return comparable_path(raw.resolve())


def _stored_path(value):
    # Configured paths may outlive a disk connection, or retain an old 8.3 name.
    try:return comparable_path(Path(value).resolve())
    except OSError:return comparable_path(Path(value).absolute())


class LibraryManager:
    def __init__(self, config):
        self.config=Path(config);self.lock=threading.RLock();self.leases={};self.cache={}
        try:data=json.loads(self.config.read_text(encoding='utf8'))
        except FileNotFoundError:data={'libraries':[],'default_id':None}
        self.items=data['libraries'];self.default_id=data.get('default_id')
        if not isinstance(self.items,list):raise ValueError('媒体库配置无效')
        for item in self.items:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str):raise ValueError('媒体库配置无效')
            self._validate(item['name'],item['path'],item['quota_bytes'],item['id'],existing=True)

    def _save(self):
        atomic_json(self.config,{'libraries':self.items,'default_id':self.default_id})

    def _validate(self,name,path,quota,exclude=None,existing=False):
        if not isinstance(name,str) or not name.strip() or len(name)>80:raise ValueError('请输入 1–80 字的库名称')
        if isinstance(quota,bool) or not isinstance(quota,int) or not 0<=quota<=1024**5:raise ValueError('存储配额无效')
        try:target=_path(path)
        except (OSError,ValueError):
            if not existing:raise
            # A disconnected/replaced library must remain editable in settings.
            target=Path(path).expanduser().absolute()
        if not existing and not target.is_dir():raise ValueError('请选择已经存在的文件夹')
        for item in self.items:
            if item['id']==exclude:continue
            other=_stored_path(item['path'])
            if target==other or target in other.parents or other in target.parents:raise ValueError('媒体库不能重复或互相嵌套')
            for prior in item.get('previous_paths',[]):
                reserved=_stored_path(prior)
                if target==reserved or target in reserved.parents or reserved in target.parents:raise ValueError('该旧路径的重新定位尚未完成，暂不能被其他媒体库复用')
        return str(target)

    def add(self,name,path,quota_bytes=0):
        with self.lock:
            target=self._validate(name,path,quota_bytes)
            item={'id':uuid.uuid4().hex,'name':name.strip(),'path':target,'quota_bytes':quota_bytes}
            self.items.append(item)
            if self.default_id is None:self.default_id=item['id']
            self._save();return dict(item)

    def update(self,library_id,name=None,path=None,quota_bytes=None):
        with self.lock:
            item=self.get(library_id)
            if any(lease.library_id==library_id for lease in self.leases.values()):raise ValueError('该库有正在进行的操作，请先暂停')
            name=item['name'] if name is None else name;path=item['path'] if path is None else path
            quota=item['quota_bytes'] if quota_bytes is None else quota_bytes
            target=self._validate(name,path,quota,library_id)
            if target!=item['path']:
                item['previous_paths']=list(dict.fromkeys([*item.get('previous_paths',[]),item['path']]))
            item.update(name=name.strip(),path=target,quota_bytes=quota)
            self.items=[item if x['id']==library_id else x for x in self.items]
            self.cache.pop(library_id,None);self._save();return dict(item)

    def get(self,library_id=None):
        with self.lock:
            item=next((x for x in self.items if x['id']==(library_id or self.default_id)),None)
            if item is None:raise ValueError('请选择有效的媒体库')
            return dict(item)

    def set_default(self,library_id):
        with self.lock:self.get(library_id);self.default_id=library_id;self._save()

    def remove(self,library_id):
        with self.lock:
            self.get(library_id)
            if any(l.library_id==library_id for l in self.leases.values()):raise ValueError('请先暂停该库的操作')
            self.items=[x for x in self.items if x['id']!=library_id]
            if self.default_id==library_id:self.default_id=self.items[0]['id'] if self.items else None
            self.cache.pop(library_id,None);self._save()

    def _usage(self,item,refresh=False):
        cached=self.cache.get(item['id'])
        if cached and not refresh and time.monotonic()-cached[0]<15:return cached[1]
        root=_path(item['path'])
        if not root.is_dir():raise OSError('媒体库离线或路径不存在')
        used=0
        for folder,dirs,files in os.walk(root,followlinks=False,onerror=lambda e:(_ for _ in ()).throw(e)):
            dirs[:]=[d for d in dirs if not is_alias(Path(folder)/d)]
            for name in files:
                file=Path(folder)/name
                if not is_alias(file):used+=file.stat().st_size
        self.cache[item['id']]=(time.monotonic(),used);return used

    def snapshot(self,refresh=False):
        result=[]
        with self.lock:
            for item in self.items:
                row=dict(item,default=item['id']==self.default_id,online=False)
                try:
                    used=self._usage(item,refresh);disk=shutil.disk_usage(item['path'])
                    quota=item['quota_bytes'];remaining=max(0,quota-used) if quota else None
                    reserved=sum(l.required for l in self.leases.values() if l.library_id==item['id'])
                    volume=Path(item['path']).stat().st_dev
                    shared_reserved=sum(l.required for l in self.leases.values() if l.volume==volume)
                    row.update(online=True,used_bytes=used,quota_remaining=remaining,disk_free=disk.free,disk_total=disk.total,reserved_bytes=reserved,available_bytes=max(0,min(remaining-reserved if remaining is not None else disk.free-shared_reserved,disk.free-shared_reserved)))
                except (OSError,ValueError) as exc:row['error']=str(exc)
                result.append(row)
        return result

    def lease(self,destination):return _Lease(self,Path(destination))


class _Lease:
    def __init__(self,manager,destination):
        self.manager=manager;self.destination=destination;self.library_id=None;self.required=0;self.volume=None

    def __enter__(self):
        with self.manager.lock:
            try:target=_path(self.destination)
            except ValueError as exc:raise Paused(str(exc)) from exc
            item=next((i for i in self.manager.items if Path(i['path']) in target.parents),None)
            if item is None:raise Paused('目标不在已配置的媒体库内')
            root=_path(item['path'])
            if not root.is_dir():raise Paused('媒体库离线，请重新定位后继续')
            self.library_id=item['id'];self.volume=root.stat().st_dev
            self.manager.leases[id(self)]=self
        return self

    def reserve(self,required):
        with self.manager.lock:
            item=self.manager.get(self.library_id)
            try:
                _path(self.destination)
                used=self.manager._usage(item,refresh=True);free=shutil.disk_usage(item['path']).free
            except (OSError,ValueError) as exc:raise Paused('媒体库不可用，请重新定位后继续') from exc
            required=max(self.required,int(required))
            other_quota=sum(l.required for l in self.manager.leases.values() if l is not self and l.library_id==self.library_id)
            other_disk=sum(l.required for l in self.manager.leases.values() if l is not self and l.volume==self.volume)
            if item['quota_bytes'] and used+other_quota+required>item['quota_bytes']:
                raise Paused('媒体库配额不足，已暂停；分段及合并需要临时空间，请提高上限或清理后继续')
            if other_disk+required>free:raise Paused('磁盘剩余空间不足，已暂停')
            self.required=required

    def __exit__(self,*args):
        with self.manager.lock:
            self.manager.leases.pop(id(self),None);self.manager.cache.pop(self.library_id,None)
