# SPDX-License-Identifier: GPL-2.0-only
"""Durable content filtering and library-scoped, ownership-checked removal."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .archive import original_path, comparable_path


def content_key(platform, message_id):
    return json.dumps([platform, str(message_id)], separators=(',', ':'))


def recycle_file(path):
    """Require Windows recycling (no permanent-delete fallback). Worker thread only."""
    if os.name != 'nt':
        raise OSError('此系统不支持 Windows 回收站')
    class GUID(ctypes.Structure):
        _fields_ = [('data', ctypes.c_ubyte * 16)]
        def __init__(self, value):
            super().__init__((ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le))
    ole = ctypes.OleDLL('ole32')
    shell = ctypes.OleDLL('shell32')
    pointer = ctypes.c_void_p
    initialized = ole.CoInitializeEx(None, 2)
    operation, item = pointer(), pointer()
    def invoke(obj, index, types, *args):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(pointer))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, pointer, *types)(table[index])(obj, *args)
    def check(result):
        if result < 0:
            raise OSError(f'回收站操作失败 (0x{result & 0xffffffff:08X})')
    try:
        check(ole.CoCreateInstance(ctypes.byref(GUID('3ad05575-8857-4850-9277-11b85bdb8e09')), None, 1,
                                  ctypes.byref(GUID('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')), ctypes.byref(operation)))
        # FOFX_RECYCLEONDELETE requires recycle, FOFX_EARLYFAILURE stops on errors.
        check(invoke(operation, 5, [ctypes.c_uint], 0x80000 | 0x100000 | 0x40 | 0x10 | 0x400 | 0x4))
        shell.SHCreateItemFromParsingName.argtypes = [ctypes.c_wchar_p, pointer, ctypes.POINTER(GUID), ctypes.POINTER(pointer)]
        check(shell.SHCreateItemFromParsingName(str(path), None, ctypes.byref(GUID('43826d1e-e718-42ee-bc55-a1e261c37bfe')), ctypes.byref(item)))
        check(invoke(operation, 18, [pointer, pointer], item, None))
        check(invoke(operation, 21, []))
        aborted = ctypes.c_int()
        check(invoke(operation, 22, [ctypes.POINTER(ctypes.c_int)], ctypes.byref(aborted)))
        if aborted.value or path.exists():
            raise OSError('文件未移入回收站，已保留原文件')
    finally:
        if item: invoke(item, 2, [])
        if operation: invoke(operation, 2, [])
        if initialized in (0, 1): ole.CoUninitialize()


def checked_path(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('媒体路径必须为绝对路径')
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, 'is_junction') and node.is_junction()):
            raise ValueError('拒绝删除符号链接或目录联接中的文件')
    return path


class RemovalManager:
    def __init__(self, store):
        self.store, self.db, self.lock = store, store.db, store.lock
        self.processing = threading.Lock()
        # Back up populated databases once, before this migration changes jobs.
        if not self.db.execute("SELECT 1 FROM catalog_migrations WHERE name='removal-v1'").fetchone():
            filename = self.db.execute('PRAGMA database_list').fetchone()[2]
            if filename and self.db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():
                import sqlite3
                backup = Path(filename + '.pre-removal-v1.bak')
                if not backup.exists():
                    target = sqlite3.connect(str(backup))
                    try: self.db.backup(target)
                    finally: target.close()
        with self.lock, self.db:
            if 'receipt_generation' not in {r[1] for r in self.db.execute('PRAGMA table_info(jobs)')}:
                self.db.execute('ALTER TABLE jobs ADD COLUMN receipt_generation INTEGER NOT NULL DEFAULT 0')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS content_filters (
                    platform TEXT NOT NULL, message_id TEXT NOT NULL, author TEXT NOT NULL,
                    text TEXT NOT NULL, created REAL NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(platform,message_id));
                CREATE TABLE IF NOT EXISTS removal_items (
                    operation TEXT NOT NULL, job_id INTEGER NOT NULL, status TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', PRIMARY KEY(operation,job_id));
                CREATE TABLE IF NOT EXISTS removal_promoted (
                    job_id INTEGER NOT NULL, generation INTEGER NOT NULL, path TEXT NOT NULL,
                    bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                    PRIMARY KEY(job_id,generation,path));
            ''')
            self.db.execute("INSERT OR IGNORE INTO catalog_migrations VALUES('removal-v1',1)")

    def validate_ids(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 50 or any(
                not isinstance(i, str) or not re.fullmatch(r'[0-9]{1,30}', i) for i in ids):
            raise ValueError('请选择当前页的 1–50 条内容')
        return list(dict.fromkeys(ids))

    def plan(self, ids, output):
        ids = self.validate_ids(ids)
        output = str(Path(output).resolve())
        rows = [r for r in self.store.rows_for_tweets(ids) if r['output'] == output and r['state'] != 'removed']
        if not rows or set(ids) - {r['tweet_id'] for r in rows}:
            raise ValueError('选中的内容不在当前媒体库中')
        if any(r['state'] == 'removing' for r in rows):
            raise ValueError('内容正在移除，请在过滤名单中查看结果或重试')
        return {'content_count': len(ids), 'media_count': len(rows), 'bytes': sum(r['bytes'] or 0 for r in rows),
                'unknown': sum(not r['bytes'] for r in rows), 'rows': rows}

    def prepare(self, ids, output):
        with self.lock, self.db:
            plan = self.plan(ids, output)
            operation = uuid.uuid4().hex
            self.store.ignore_tweets(ids)
            for row in plan['rows']:
                self.db.execute("UPDATE content_filters SET reason='removed' WHERE platform='x' AND message_id=?", (row['tweet_id'],))
                self.db.execute("INSERT INTO removal_items(operation,job_id,status) VALUES(?,?,'pending')", (operation,row['id']))
                self.db.execute("UPDATE jobs SET state='removing',force_download=0,receipt_generation=receipt_generation+1 WHERE id=?", (row['id'],))
            return {'id': operation, **{k:v for k,v in plan.items() if k != 'rows'}}

    def status(self, operation):
        with self.lock:
            rows = [dict(r) for r in self.db.execute('SELECT job_id,status,error FROM removal_items WHERE operation=?', (operation,))]
        if not rows: raise ValueError('找不到移除操作')
        return {'id': operation, 'items': rows, 'pending': sum(r['status']=='pending' for r in rows),
                'failed': sum(r['status']=='error' for r in rows), 'done': sum(r['status']=='done' for r in rows), 'total':len(rows)}

    def record_promoted(self, row, destination, result):
        with self.lock, self.db:
            current=self.store.get_many([row['id']])[0]
            if current['state']=='removing' and current['receipt_generation']==row.get('receipt_generation',0)+1:
                self.db.execute('INSERT OR REPLACE INTO removal_promoted VALUES(?,?,?,?,?)',
                    (row['id'],current['receipt_generation'],str(destination),result['bytes'],result['sha256']))

    def _recycle_target(self, target, proof, row, recycle):
        target=checked_path(target)
        with self.lock:
            shared = self.db.execute("SELECT * FROM jobs WHERE id!=? AND state NOT IN ('removed','removing')", (row['id'],)).fetchall()
            for other in shared:
                other = dict(other)
                other_path = Path(other['local_path']) if other.get('local_path') else original_path(other)
                if comparable_path(other_path.resolve()) == comparable_path(target.resolve()):
                    return '共享文件保留，已移除当前关联'
        if target.exists():
            if not target.is_file(): raise ValueError('媒体路径不是普通文件')
            identity = target.stat()
            # Windows path stat/fstat report ctime differently after rename; use file ID + content metadata.
            signature=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns)
            if not proof['sha256'] or identity.st_size != proof['bytes']:
                raise ValueError('无法确认媒体归属或文件已变化，保留文件')
            digest = hashlib.sha256()
            with target.open('rb') as handle:
                if signature(os.fstat(handle.fileno()))!=signature(identity):
                    raise ValueError('文件在打开时被替换，保留文件')
                for block in iter(lambda: handle.read(1024*1024), b''): digest.update(block)
                after_read=os.fstat(handle.fileno())
            if digest.hexdigest() != proof['sha256']:
                raise ValueError('文件校验不匹配，保留文件')
            checked_path(target)
            current = target.stat()
            if signature(identity)!=signature(after_read) or signature(identity)!=signature(current):
                raise ValueError('文件在检查期间变化，保留文件')
            recycle(target)
            if target.exists(): raise OSError('回收站未移除目标文件，已保留移除记录以供重试')
        elif not Path(row['output']).is_dir():
            raise ValueError('媒体库离线，不能确认文件已删除')
        else:
            return '登记文件不存在；未查找或删除手动移动到其他位置的文件'
        return ''

    def _cleanup(self, row, recycle):
        checked_path(row['output'])
        original = checked_path(original_path(row))
        target = checked_path(Path(row['local_path']) if row.get('local_path') else original)
        if row.get('relocation_source'):
            raise ValueError('媒体转移尚未完成，请先完成转移恢复')
        targets={str(target):row}
        with self.lock:
            for proof in self.db.execute('SELECT path,bytes,sha256 FROM removal_promoted WHERE job_id=? AND generation=?',(row['id'],row['receipt_generation'])):
                targets[proof['path']]=dict(proof)
        notes=[]
        for path,proof in targets.items():
            note=self._recycle_target(Path(path),proof,row,recycle)
            if note:notes.append(note)
        # Remove only the downloader's known checkpoint filenames, never arbitrary directories.
        staging=original.with_suffix(original.suffix+'.incoming')
        bases=[original,staging] if row.get('media_type')=='image' else [original]
        for base in bases:
            parts=checked_path(base.with_suffix(base.suffix+'.parts'))
            if parts.exists():
                entries = list(parts.iterdir())
                if any(not p.is_file() or p.is_symlink() or not (p.name == 'manifest.json' or re.fullmatch(r'\d+\.part(?:\.json)?', p.name)) for p in entries):
                    raise ValueError('分片目录含未知文件，保留目录')
                for p in entries: checked_path(p).unlink()
                parts.rmdir()
            joining=checked_path(base.with_suffix(base.suffix+'.joining'))
            if joining.exists():
                if not joining.is_file():raise ValueError('暂存路径不是普通文件')
                joining.unlink()
        if row.get('media_type')=='image':
            for temporary in (staging,staging.with_suffix(staging.suffix+'.verified.json')):
                checked_path(temporary)
                if temporary.exists():
                    if not temporary.is_file():raise ValueError('图片暂存路径不是普通文件')
                    temporary.unlink()
        return '；'.join(dict.fromkeys(notes))

    def process(self, operation, recycle=recycle_file, runner=None):
        with self.processing:
            status = self.status(operation)
            ids = [r['job_id'] for r in status['items'] if r['status'] != 'done']
            if runner is not None:
                runner.cancel_jobs(ids)
                while runner.active_jobs(ids): time.sleep(.05)
            for job in ids:
                row = self.store.get_many([job])[0]
                try:
                    note = self._cleanup(row, recycle)
                    with self.lock, self.db:
                        self.db.execute("UPDATE jobs SET state='removed',error='',force_download=0 WHERE id=?", (job,))
                        self.db.execute("UPDATE removal_items SET status='done',error=? WHERE operation=? AND job_id=?", (note,operation,job))
                except Exception as exc:
                    with self.lock, self.db:
                        self.db.execute("UPDATE removal_items SET status='error',error=? WHERE operation=? AND job_id=?", (str(exc),operation,job))
            return self.status(operation)

    def page(self, query='', offset=0, limit=50):
        # Old ignored_tweets remains the X queue compatibility index.
        offset, limit = max(0,int(offset)), min(50,max(1,int(limit)))
        words = str(query)[:200].casefold().split()
        with self.lock:
            rows = [dict(r) for r in self.db.execute('SELECT * FROM content_filters ORDER BY created DESC,platform,message_id')]
            rows = [r for r in rows if all(w in (r['author']+' '+r['text']+' '+r['message_id']).casefold() for w in words)]
            total = len(rows)
            if offset >= total: offset = max(0,(total-1)//limit)*limit
            items = []
            for row in rows[offset:offset+limit]:
                operations = [dict(r) for r in self.db.execute('''SELECT DISTINCT i.operation,i.status,i.error FROM removal_items i
                    JOIN jobs j ON j.id=i.job_id WHERE j.tweet_id=? AND i.status!='done' ''', (row['message_id'],))]
                notes = [r[0] for r in self.db.execute("SELECT DISTINCT i.error FROM removal_items i JOIN jobs j ON j.id=i.job_id WHERE j.tweet_id=? AND i.status='done' AND i.error!=''", (row['message_id'],))]
                items.append({**row, 'key':content_key(row['platform'],row['message_id']), 'operations':operations,'notes':notes})
            return {'items':items,'total':total,'offset':offset}

    def clear(self, keys=None):
        with self.lock, self.db:
            if keys is None:
                keys = [content_key(r[0],r[1]) for r in self.db.execute('SELECT platform,message_id FROM content_filters')]
            if not isinstance(keys,list) or len(keys)>10000: raise ValueError('过滤名单选择无效')
            identities=[]
            for key in keys:
                try: platform, message = json.loads(key)
                except Exception: raise ValueError('过滤内容标识无效') from None
                if platform != 'x' or not isinstance(message,str) or not re.fullmatch(r'[0-9]{1,30}', message):
                    raise ValueError('过滤内容标识无效')
                if self.db.execute("SELECT 1 FROM removal_items i JOIN jobs j ON j.id=i.job_id WHERE j.tweet_id=? AND i.status!='done' LIMIT 1", (message,)).fetchone():
                    raise ValueError('请先完成或重试该内容的移除，再取消过滤')
                identities.append(message)
            self.store.ignore_tweets(identities, False)
            return len(identities)
