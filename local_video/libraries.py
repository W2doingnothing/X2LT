# SPDX-License-Identifier: GPL-2.0-only
"""Persistent independent library quotas with process-wide peak-space leases."""
from dataclasses import asdict, dataclass
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


def _scan_mode(value):
    if value not in ('media','comic'):
        raise ValueError('媒体库扫描模式无效')
    return value


@dataclass(frozen=True)
class Capacity:
    path: str
    measured_at: int
    used: int
    free: int
    total: int
    volume: int


class LibraryManager:
    def __init__(self, config):
        self.config=Path(config);self.lock=threading.RLock();self.leases={};self.cache={}
        self.capacity_file=self.config.with_suffix('.capacity.json')
        self.layout_operations=set()
        self.last_capacity={}
        self.capacity_errors={}
        self.capacity_revision=0
        self.capacity_worker=None
        self.capacity_stop=threading.Event()
        self.measure_lock=threading.Lock()
        try:data=json.loads(self.config.read_text(encoding='utf8'))
        except FileNotFoundError:data={'libraries':[],'default_id':None}
        self.items=data['libraries'];self.default_id=data.get('default_id')
        if not isinstance(self.items,list):raise ValueError('媒体库配置无效')
        from .emoji import validate_emoji
        migrated=False
        for item in self.items:
            if not isinstance(item,dict) or not isinstance(item.get('id'),str):raise ValueError('媒体库配置无效')
            self._validate(item['name'],item['path'],item['quota_bytes'],item['id'],existing=True)
            if 'emoji' not in item:
                previous=item.get('cover') or {}
                try:item['emoji']=validate_emoji(previous.get('value') if previous.get('kind')=='emoji' else '📁')
                except ValueError:item['emoji']='📁'
                migrated=True
            else:validate_emoji(item['emoji'])
            item['scan_mode'] = _scan_mode(item.get('scan_mode','media'))
            item['auto_flatten'] = item.get('auto_flatten') is True
            item['auto_flatten_pending'] = item.get('auto_flatten_pending') is True
        if migrated:self._save()
        self._load_capacity()

    def _load_capacity(self):
        try:
            if self.capacity_file.stat().st_size > 1024 * 1024:
                return
            saved = json.loads(self.capacity_file.read_text(encoding='utf8'))
        except (OSError, ValueError):
            return
        if not isinstance(saved,dict):
            return
        for item in self.items:
            row = saved.get(item['id'])
            if not isinstance(row,dict) or row.get('path') != item['path']:
                continue
            keys = ('measured_at','used','free','total','volume')
            if any(type(row.get(key)) is not int or not 0 <= row[key] < 2**64 for key in keys):
                continue
            if row['free'] <= row['total']:
                self.last_capacity[item['id']] = Capacity(item['path'],*(row[key] for key in keys))

    def _save(self):
        atomic_json(self.config,{'libraries':self.items,'default_id':self.default_id})

    def validate_migration_target(self, library_id, path):
        with self.lock:
            item = self.get(library_id)
            return self._validate(item['name'], path, item['quota_bytes'], library_id)

    def rebind_migration(self, library_id, source, destination):
        with self.lock:
            item = self.get(library_id)
            if Path(item['path']) == destination:
                return
            if Path(item['path']) != source or item.get('previous_paths'):
                raise ValueError('迁移期间媒体库配置已变更')
            target = self._validate(item['name'], destination, item['quota_bytes'], library_id)
            updated = dict(item, path=target)
            items = [updated if entry['id'] == library_id else entry for entry in self.items]
            atomic_json(self.config, {'libraries': items, 'default_id': self.default_id})
            self.items = items
            self.last_capacity.pop(library_id, None)
            self.invalidate_capacity(library_id)

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

    def appearance(self,library_id,emoji=None,cover='__keep__'):
        from .emoji import validate_emoji
        with self.lock:
            original=self.get(library_id)
            updated={**original}
            if emoji is not None:updated['emoji']=validate_emoji(emoji)
            if cover!='__keep__':updated['cover']=cover
            items=[updated if item['id']==library_id else item for item in self.items]
            atomic_json(self.config,{'libraries':items,'default_id':self.default_id})
            self.items=items
            return dict(updated)

    def add(self,name,path,quota_bytes=0,emoji='📁',scan_mode='media'):
        from .emoji import validate_emoji
        emoji=validate_emoji(emoji)
        scan_mode=_scan_mode(scan_mode)
        with self.lock:
            target=self._validate(name,path,quota_bytes)
            item={'id':uuid.uuid4().hex,'name':name.strip(),'path':target,'quota_bytes':quota_bytes,'emoji':emoji,
                  'scan_mode':scan_mode,'auto_flatten':False,'auto_flatten_pending':False}
            self.items.append(item)
            if self.default_id is None:self.default_id=item['id']
            self._save();return dict(item)

    def update(self,library_id,name=None,path=None,quota_bytes=None,emoji=None,scan_mode=None):
        from .emoji import validate_emoji
        if emoji is not None:emoji=validate_emoji(emoji)
        if scan_mode is not None:scan_mode=_scan_mode(scan_mode)
        with self.lock:
            item=self.get(library_id)
            if any(lease.library_id==library_id for lease in self.leases.values()):raise ValueError('该库有正在进行的操作，请先暂停')
            name=item['name'] if name is None else name;path=item['path'] if path is None else path
            quota=item['quota_bytes'] if quota_bytes is None else quota_bytes
            target=self._validate(name,path,quota,library_id)
            if target!=item['path']:
                item['previous_paths']=list(dict.fromkeys([*item.get('previous_paths',[]),item['path']]))
            item.update(name=name.strip(),path=target,quota_bytes=quota)
            if emoji is not None:item['emoji']=emoji
            if scan_mode is not None:item['scan_mode']=scan_mode
            self.items=[item if x['id']==library_id else x for x in self.items]
            self.invalidate_capacity(library_id);self._save();return dict(item)

    def get(self,library_id=None):
        with self.lock:
            item=next((x for x in self.items if x['id']==(library_id or self.default_id)),None)
            if item is None:raise ValueError('请选择有效的媒体库')
            return dict(item)

    def auto_flatten_pending(self, library_id=None):
        with self.lock:
            return any(
                (library_id is None or item['id'] == library_id)
                and (item.get('auto_flatten_pending') or item['id'] in self.layout_operations)
                for item in self.items
            )

    def layout_for_output(self, output):
        path = _stored_path(output)
        with self.lock:
            return next((dict(item) for item in self.items if str(path) == item['path']), None)

    def begin_layout(self, library_id):
        with self.lock:
            self.get(library_id)
            if library_id in self.layout_operations or any(
                    lease.library_id == library_id for lease in self.leases.values()):
                raise ValueError('请先暂停该媒体库的操作')
            self.layout_operations.add(library_id)

    def end_layout(self, library_id):
        with self.lock:
            self.layout_operations.discard(library_id)
            self.invalidate_capacity(library_id)

    def save_layout(self, library_id, enabled, pending, error=''):
        with self.lock:
            original = self.get(library_id)
            updated = dict(original, auto_flatten=enabled, auto_flatten_pending=pending, auto_flatten_error=error)
            items = [updated if item['id'] == library_id else item for item in self.items]
            atomic_json(self.config, {'libraries': items, 'default_id': self.default_id})
            self.items = items
            return dict(updated)

    def set_default(self,library_id):
        with self.lock:self.get(library_id);self.default_id=library_id;self._save()

    def remove(self,library_id):
        with self.lock:
            self.get(library_id)
            if any(l.library_id==library_id for l in self.leases.values()):raise ValueError('请先暂停该库的操作')
            self.items=[x for x in self.items if x['id']!=library_id]
            if self.default_id==library_id:self.default_id=self.items[0]['id'] if self.items else None
            self.invalidate_capacity(library_id)
            self.last_capacity.pop(library_id,None)
            self._save()

    def invalidate_capacity(self, library_id=None):
        with self.lock:
            self.capacity_revision += 1
            if library_id is None:
                self.cache.clear()
                self.capacity_errors.clear()
            else:
                self.cache.pop(library_id,None)
                self.capacity_errors.pop(library_id,None)

    @staticmethod
    def _usage(root, stop=None):
        if not root.is_dir():raise OSError('媒体库离线或路径不存在')
        used=0
        for folder,dirs,files in os.walk(root,followlinks=False,onerror=lambda e:(_ for _ in ()).throw(e)):
            if stop and stop.is_set():
                raise InterruptedError('容量检查已取消')
            dirs[:]=[d for d in dirs if not is_alias(Path(folder)/d)]
            for name in files:
                if stop and stop.is_set():
                    raise InterruptedError('容量检查已取消')
                file=Path(folder)/name
                if not is_alias(file):used+=file.stat().st_size
        return used

    def _measure(self, item):
        root = _path(item['path'])
        used = self._usage(root,self.capacity_stop)
        disk = shutil.disk_usage(root)
        return Capacity(item['path'],int(time.time()),used,disk.free,disk.total,root.stat().st_dev)

    def _pending_capacity(self, identifier):
        return identifier not in self.cache and identifier not in self.capacity_errors

    def _refresh_capacity(self, *, force, notify=None):
        # Disk work and persistence are never performed under the library or desktop lock.
        with self.measure_lock:
            with self.lock:
                items = [dict(item) for item in self.items if force or self._pending_capacity(item['id'])]
            changed = False
            for item in items:
                if self.capacity_stop.is_set():
                    break
                identifier = item['id']
                with self.lock:
                    revision = self.capacity_revision
                    if not force and not self._pending_capacity(identifier):
                        continue
                try:
                    sample, error = self._measure(item), ''
                except (OSError, ValueError) as exc:
                    sample, error = None, str(exc)
                with self.lock:
                    current = next((row for row in self.items if row['id'] == identifier),None)
                    if not current or current['path'] != item['path'] or revision != self.capacity_revision:
                        continue
                    if self.capacity_stop.is_set():
                        break
                    if sample is None:
                        self.cache.pop(identifier,None)
                        self.capacity_errors[identifier] = error
                    else:
                        self.capacity_errors.pop(identifier,None)
                        self.cache[identifier] = self.last_capacity[identifier] = sample
                        changed = True
                if notify:
                    notify()
            if changed and not self.capacity_stop.is_set():
                with self.lock:
                    payload = {item['id']:asdict(sample) for item in self.items
                               if (sample := self.last_capacity.get(item['id'])) and sample.path == item['path']}
                try:
                    atomic_json(self.capacity_file,payload)
                except OSError:
                    pass  # A cache persistence failure leaves the current measurements available.

    def refresh_background(self, notify):
        with self.lock:
            if self.capacity_worker is not None or self.capacity_stop.is_set() or not any(
                    self._pending_capacity(item['id']) for item in self.items):
                return

            def work():
                try:
                    self._refresh_capacity(force=False,notify=notify)
                finally:
                    with self.lock:
                        self.capacity_worker = None
                        pending = any(self._pending_capacity(item['id']) for item in self.items)
                    if pending and not self.capacity_stop.is_set():
                        notify()

            self.capacity_worker = threading.Thread(target=work,daemon=True,name='x2lt-library-capacity')
            self.capacity_worker.start()

    def snapshot(self,refresh=False,cached_only=False):
        if refresh or not cached_only:
            self._refresh_capacity(force=refresh)
        result=[]
        with self.lock:
            for item in self.items:
                identifier = item['id']
                pending = self._pending_capacity(identifier)
                sample = self.cache.get(identifier) or (self.last_capacity.get(identifier) if pending else None)
                if sample and sample.path != item['path']:
                    sample = None
                row = dict(item,default=identifier == self.default_id,online=identifier in self.cache,
                           capacity_pending=pending,capacity_stale=pending and sample is not None)
                if sample:
                    quota=item['quota_bytes'];remaining=max(0,quota-sample.used) if quota else None
                    reserved=sum(l.required for l in self.leases.values() if l.library_id==item['id'])
                    disk_free=sample.free-sum(l.required for l in self.leases.values() if l.volume==sample.volume)
                    available=min(remaining-reserved,disk_free) if remaining is not None else disk_free
                    row.update(used_bytes=sample.used,quota_remaining=remaining,disk_free=sample.free,
                               disk_total=sample.total,reserved_bytes=reserved,available_bytes=max(0,available),
                               capacity_checked_at=sample.measured_at)
                if identifier in self.capacity_errors:
                    row['error']=self.capacity_errors[identifier]
                result.append(row)
        return result

    def close(self):
        self.capacity_stop.set()
        with self.lock:
            worker = self.capacity_worker
        if worker is not None:
            worker.join(timeout=.2)

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
            if self.manager.auto_flatten_pending(item['id']):
                raise Paused('媒体库正在整理，请完成或重试自动集中后继续')
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
                used=self.manager._usage(_path(item['path'])) if item['quota_bytes'] else 0
                free=shutil.disk_usage(item['path']).free
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
            self.manager.leases.pop(id(self),None)
            self.manager.invalidate_capacity(self.library_id)
