# SPDX-License-Identifier: GPL-2.0-only
"""Media library lifecycle and receipt-preserving path repair."""
from pathlib import Path
import json
from ..archive import original_path, manifest, read_json, comparable_path
from ..local_collection import file_signature, path_key
from ..local_import_files import selected_path
from ..security import atomic_json


class LibraryService:
    def __init__(self, manager, store, settings, native, runtime, data, home):
        self.manager,self.store,self.settings=manager,store,settings
        self.native,self.runtime=native,runtime
        self.errors={}
        marker=Path(data)/'libraries-initialized'
        if not manager.items and not marker.exists():
            path=Path(settings.values.get('output') or Path(home)/'downloads')
            if not path.exists() and not settings.values.get('output'):path.mkdir(parents=True)
            if path.is_dir():manager.add('默认媒体库',path)
            elif path.is_absolute():
                manager.items=[{'id':'legacy','name':'原下载库（离线）','path':str(path),'quota_bytes':0,'emoji':'📁'}]
                manager.default_id='legacy';manager._save()
            marker.touch()
        self.repair_paths()

    def add(self, p):
        path=str(Path(p['path']).resolve())
        if path not in self.native.approved_paths:raise ValueError('请使用文件夹选择按钮选取路径')
        return self.manager.add(p['name'],path,p.get('quota_bytes',0),p.get('emoji','📁'),p.get('scan_mode','media'))

    def update(self, p):
        if self.runtime.busy():raise ValueError('请先暂停操作再修改媒体库')
        path=p.get('path')
        if path is not None and str(Path(path).resolve()) not in self.native.approved_paths:raise ValueError('请通过文件夹选择器重新定位')
        if path is not None:self.check_rebind(self.manager.get(p['id'])['path'],str(Path(path).resolve()))
        result=self.manager.update(p['id'],p.get('name'),path,p.get('quota_bytes'),p.get('emoji'),p.get('scan_mode'))
        self.repair_paths()
        if p['id'] in self.errors:raise ValueError(self.errors[p['id']])
        return result

    def set_default(self, p):
        self.manager.set_default(p['id']);return True

    def remove(self, p):
        if self.runtime.busy():raise ValueError('请先暂停操作再移除媒体库')
        self.manager.remove(p['id']);return True

    def refresh(self, p):return self.manager.snapshot(refresh=True)

    def check_rebind(self,old,new):
        if old==new:return
        with self.store.lock:
            collision=self.store.db.execute('SELECT 1 FROM jobs a JOIN jobs b ON a.tweet_id=b.tweet_id AND a.media_id=b.media_id AND a.quality=b.quality WHERE a.output=? AND b.output=? LIMIT 1',(old,new)).fetchone()
            if collision:raise ValueError('目标路径已有独立下载记录，不能自动合并；请选择原媒体库的实际位置')


    def repair_paths(self):
        self.errors.clear()
        for library in self.manager.items:
            try:
                new=Path(library['path'])
                for old_value in library.get('previous_paths',[]):
                    old=Path(old_value)
                    if old==new:continue
                    self.check_rebind(str(old),str(new))
                    def remap(value):
                        if not value:return value
                        path=comparable_path(Path(value))
                        return str(new/path.relative_to(old)) if path.is_relative_to(old) else value
                    with self.store.lock:
                        rows=[dict(r) for r in self.store.db.execute('SELECT * FROM jobs WHERE output=?',(str(old),))]
                        for row in rows:
                            updated={**row,'output':str(new)}
                            receipt_path=manifest(original_path(updated));receipt=read_json(receipt_path)
                            if receipt.get('archive_job')==row['id'] and receipt.get('sha256')==row['sha256'] and receipt.get('archive_output') in (str(old),str(new)):
                                receipt['archive_output']=str(new)
                                for key in ('local_path','pending_source','pending_target'):
                                    if key in receipt:receipt[key]=remap(receipt[key])
                                atomic_json(receipt_path,receipt)
                        with self.store.db:
                            for row in rows:self.store.db.execute('UPDATE jobs SET output=?,local_path=?,relocation_source=? WHERE id=?',(str(new),remap(row['local_path']),remap(row['relocation_source']),row['id']))
                            self._repair_telegram_paths(library['id'], old, new)
                if library.get('previous_paths'):
                    library['previous_paths']=[]
                    self.manager._save()
            except Exception as exc:self.errors[library['id']]=f'重新定位记录尚未完成，请修复路径后重试：{exc}'

    def _repair_telegram_paths(self, library_id, old, new):
        db = self.store.db
        saved = db.execute('SELECT local_location,model_location FROM telegram_saved WHERE library=?',
                           (library_id,)).fetchall()
        for entry in saved:
            db.execute('UPDATE model_locations SET output=? WHERE id=? AND output=?',
                       (str(new), entry['model_location'], str(old)))
            row = db.execute('SELECT path,root,sig FROM local_locations WHERE id=?',
                             (entry['local_location'],)).fetchone()
            if not row or row['root'] != str(old):
                continue
            path = Path(row['path'])
            if not path.is_relative_to(old):
                continue
            moved = new / path.relative_to(old)
            # Keep the old reference if the file was copied or replaced. The
            # local relocation dialog can verify those cases by full digest.
            try:
                if path.exists() or selected_path(moved) != moved or json.dumps(file_signature(moved)) != row['sig']:
                    continue
            except (OSError, ValueError):
                continue
            if db.execute('SELECT 1 FROM local_locations WHERE path_key=?', (path_key(moved),)).fetchone():
                continue
            db.execute('UPDATE local_locations SET path=?,path_key=?,root=? WHERE id=?',
                       (str(moved), path_key(moved), str(new), entry['local_location']))
