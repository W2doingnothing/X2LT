# SPDX-License-Identifier: GPL-2.0-only
"""Copy, verify and rebind a library before removing its original files."""
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import threading
from uuid import uuid4

from ..archive import comparable_path, manifest, original_path
from ..images import is_alias
from ..libraries import _path
from ..local_collection import path_key
from ..transfer import Paused


BLOCK = 1024 * 1024
SCHEMA = '''
CREATE TABLE IF NOT EXISTS library_moves(
    id TEXT PRIMARY KEY, token TEXT NOT NULL,
    source TEXT NOT NULL, destination TEXT NOT NULL, source_id TEXT NOT NULL, destination_id TEXT NOT NULL,
    phase TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', file_count INTEGER NOT NULL,
    total_bytes INTEGER NOT NULL, free_bytes INTEGER NOT NULL,
    copied_files INTEGER NOT NULL DEFAULT 0, copied_bytes INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS library_move_files(
    library TEXT NOT NULL, relative TEXT NOT NULL, directory INTEGER NOT NULL, signature TEXT NOT NULL,
    size INTEGER NOT NULL, original_hash TEXT NOT NULL DEFAULT '', target_hash TEXT NOT NULL DEFAULT '',
    published INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(library,relative));
'''


def _info(path):
    if any(is_alias(node) for node in (path, *path.parents)):
        raise ValueError(f'媒体库迁移不支持符号链接或目录联接：{path}')
    return path.lstat()


def _identity(info):
    return json.dumps([info.st_dev, info.st_ino])


def _signature(info):
    if stat.S_ISDIR(info.st_mode):
        return _identity(info)
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('媒体库含有非普通文件')
    return json.dumps([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])


def _path_under(value, root):
    if not value:
        return None
    path = comparable_path(Path(value))
    if path.is_absolute() and '..' not in path.parts and path.is_relative_to(root):
        return path.relative_to(root)
    return None


def _remap(value, old, new):
    relative = _path_under(value, old)
    return str(new / relative) if relative is not None else value


def _overlap(first, second):
    return first.is_relative_to(second) or second.is_relative_to(first)


class LibraryMigrationService:
    def __init__(self, manager, store, native, runtime, data, settings=None, *, idle_check=None):
        self.manager, self.store, self.native, self.runtime = manager, store, native, runtime
        self.data, self.settings, self.idle_check = Path(data).resolve(), settings, idle_check
        self.cancel = threading.Event()
        self.active = False
        with self.store.lock:
            self.store.db.executescript(SCHEMA)
            with self.store.db:
                self.store.db.execute("""UPDATE library_moves SET status='paused', error='迁移中断，进度已保留'
                                         WHERE status IN ('copying','rebinding','cleaning')""")
        # A crash can leave the committed catalog ahead of the library config.
        # Publish the matching root on restart without cleaning any files.
        for move in self._rows("SELECT * FROM library_moves WHERE phase='publish'"):
            try:
                source, target = self._roots(move)
                self._publish_root(move['id'], source, target)
                self._write("UPDATE library_moves SET phase='clean' WHERE id=?", (move['id'],))
            except (OSError, ValueError) as exc:
                self._set_status(move['id'], 'failed', str(exc)[:1600])

    def _rows(self, sql, args=()):
        with self.store.lock:
            return [dict(row) for row in self.store.db.execute(sql, args)]

    def _write(self, sql, args=()):
        with self.store.lock, self.store.db:
            self.store.db.execute(sql, args)

    def pending(self, identifier=None):
        return bool(self._rows("""SELECT 1 FROM library_moves WHERE phase NOT IN ('done','abandoned')
                                  AND (? IS NULL OR id=?) LIMIT 1""", (identifier, identifier)))

    def _get(self, identifier):
        return next(iter(self._rows('SELECT * FROM library_moves WHERE id=?', (identifier,))), None)

    @staticmethod
    def _public(move):
        if move:
            public = {key: move[key] for key in ('id', 'source', 'destination', 'status', 'error', 'file_count',
                                               'total_bytes', 'free_bytes', 'copied_files', 'copied_bytes')}
            public['can_abandon'] = (move.get('phase') == 'copy' and move['status'] in ('paused', 'failed'))
            return public
        return None

    def status(self, request):
        return self._public(self._get(request['id']))

    def _idle(self):
        if self.active or self.runtime.busy() or self.runtime.removing():
            raise ValueError('请先等待或暂停当前操作')
        if self.idle_check is not None and not self.idle_check():
            raise ValueError('请先等待或暂停下载、入库等操作')

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Paused('媒体库迁移已暂停，进度已保留')

    def _inventory(self, source, *, cancellable=False):
        pending = [source]
        while pending:
            folder = pending.pop()
            if not stat.S_ISDIR(_info(folder).st_mode):
                raise ValueError(f'媒体库目录已变化：{folder}')
            for child in sorted(folder.iterdir()):
                if cancellable:
                    self._check_cancel()
                info = child.lstat()
                if stat.S_ISLNK(info.st_mode) or (
                        getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                    raise ValueError(f'媒体库迁移不支持符号链接或目录联接：{child}')
                directory = stat.S_ISDIR(info.st_mode)
                yield str(child.relative_to(source)), directory, _signature(info), 0 if directory else info.st_size
                if directory:
                    pending.append(child)

    def _survey(self, request):
        if self.pending():
            raise ValueError('已有未完成的媒体库迁移，请先继续该迁移')
        item = self.manager.get(request['id'])
        if item.get('previous_paths') or self.manager.auto_flatten_pending(item['id']):
            raise ValueError('请先完成原有的目录整理或重新定位')
        source, target = _path(item['path']), _path(request['path'])
        if str(target) not in self.native.approved_paths:
            raise ValueError('请通过文件夹选择器选取迁移目标')
        if not source.is_dir() or not target.is_dir():
            raise ValueError('源目录和目标目录必须在线且可用')
        if _overlap(source, target) or _overlap(source, self.data) or _overlap(target, self.data):
            raise ValueError('媒体库、迁移目标及程序数据目录不能相同或相互嵌套')
        self.manager.validate_migration_target(item['id'], target)
        source_id, target_id = _identity(_info(source)), _identity(_info(target))
        if source_id == target_id:
            raise ValueError('请选择与当前媒体库不同的目录')
        entries = list(self._inventory(source))
        # An existing top-level entry already conflicts with its complete subtree.
        for destination in (target / relative for relative, _, _, _ in entries if Path(relative).parent == Path('.')):
            try:
                _info(destination)
            except FileNotFoundError:
                continue
            raise ValueError(f'迁移目标已有同名文件或目录：{destination}')
        self._check_catalog(item['id'], source, target, preflight=True)
        total = sum(entry[3] for entry in entries)
        free = self._check_space(target, total)
        return (dict(id=item['id'], token=uuid4().hex, source=str(source), destination=str(target),
                     source_id=source_id, destination_id=target_id, status='planned', error='',
                     file_count=sum(not entry[1] for entry in entries), total_bytes=total, free_bytes=free,
                     copied_files=0, copied_bytes=0), entries)

    def _check_space(self, target, required):
        disk = shutil.disk_usage(target)
        with self.manager.lock:
            current_volume = target.stat().st_dev
            reserved = sum(lease.required for lease in self.manager.leases.values() if lease.volume == current_volume)
        free = max(0, disk.free - reserved)
        if free < required:
            raise ValueError(f'目标磁盘空间不足：需要 {required} 字节，可用 {free} 字节')
        return free

    def plan(self, request):
        move, _ = self._survey(request)
        return self._public(move)

    def start(self, request):
        self._idle()
        move, entries = self._survey(request)
        identifier = move['id']
        self.manager.begin_layout(identifier)
        try:
            with self.store.lock, self.store.db:
                if self.pending():
                    raise ValueError('已有未完成的媒体库迁移')
                self.store.db.execute('DELETE FROM library_move_files WHERE library=?', (identifier,))
                self.store.db.execute('''INSERT OR REPLACE INTO library_moves(id,token,source,destination,source_id,
                    destination_id,phase,status,file_count,total_bytes,free_bytes) VALUES(:id,:token,:source,:destination,
                    :source_id,:destination_id,'copy','copying',:file_count,:total_bytes,:free_bytes)''', move)
                self.store.db.executemany('''INSERT INTO library_move_files(library,relative,directory,signature,size)
                    VALUES(?,?,?,?,?)''', ((identifier, *entry) for entry in entries))
            return self._launch(identifier, 'copying')
        except BaseException:
            self.manager.end_layout(identifier)
            raise

    def retry(self, request):
        self._idle()
        identifier = request['id']
        move = self._get(identifier)
        if not move or move['status'] not in ('paused', 'failed'):
            raise ValueError('找不到可继续的媒体库迁移')
        if self.manager.auto_flatten_pending(identifier):
            raise ValueError('请先完成原有的目录整理')
        self.manager.begin_layout(identifier)
        try:
            return self._launch(identifier, {'copy': 'copying', 'publish': 'rebinding', 'clean': 'cleaning'}[move['phase']])
        except BaseException:
            self.manager.end_layout(identifier)
            raise

    def abandon(self, request):
        identifier = request['id']
        with self.store.lock, self.store.db:
            cursor = self.store.db.execute("""UPDATE library_moves SET phase='abandoned',status='abandoned',error=''
                WHERE id=? AND phase='copy' AND status IN ('paused','failed')""", (identifier,))
            if not cursor.rowcount:
                raise ValueError('仅可放弃尚未切换路径的暂停或失败迁移')
            self.store.db.execute('DELETE FROM library_move_files WHERE library=?', (identifier,))
        self.runtime.emit({'type': 'changed'})
        return self.status(request)

    def _launch(self, identifier, status):
        self.cancel.clear()
        self.active = True
        try:
            self._set_status(identifier, status)
            self.runtime.runner = self
            self.runtime.worker = self.runtime.thread_factory(target=self._run, args=(identifier,),
                                                              daemon=True, name='x2lt-library-move')
            self.runtime.worker.start()
        except BaseException:
            self.active = False
            self._set_status(identifier, 'paused', '迁移任务未能启动，请重试')
            raise
        return self.status({'id': identifier})

    def _set_status(self, identifier, status, error=''):
        self._write('UPDATE library_moves SET status=?,error=? WHERE id=?', (status, error, identifier))
        self.runtime.emit({'type': 'changed'})

    def _roots(self, move):
        source, target = Path(move['source']), Path(move['destination'])
        for path, expected in ((source, move['source_id']), (target, move['destination_id'])):
            info = _info(path)
            if not stat.S_ISDIR(info.st_mode) or _identity(info) != expected:
                raise ValueError(f'迁移目录已替换或不在线：{path}')
        return source, target

    def _run(self, identifier):
        try:
            move = self._get(identifier)
            source, target = self._roots(move)
            if move['phase'] == 'copy':
                self._copy(move, source, target)
                self._set_status(identifier, 'rebinding')
                self._bind(move, source, target)
            if move['phase'] in ('copy', 'publish'):
                self._publish_root(identifier, source, target)
                self._write("UPDATE library_moves SET phase='clean' WHERE id=?", (identifier,))
            self._set_status(identifier, 'cleaning')
            self._clean(move, source, target)
            with self.store.lock, self.store.db:
                self.store.db.execute('DELETE FROM library_move_files WHERE library=?', (identifier,))
                self.store.db.execute("UPDATE library_moves SET phase='done',status='done',error='' WHERE id=?",
                                      (identifier,))
        except Paused as exc:
            self._set_status(identifier, 'paused', str(exc))
        except Exception as exc:
            self._set_status(identifier, 'failed', str(exc)[:1600])
        finally:
            self.active = False
            self.manager.end_layout(identifier)
            self.runtime.emit({'type': 'changed'})

    def _files(self, identifier):
        return self._rows('SELECT * FROM library_move_files WHERE library=? ORDER BY relative', (identifier,))

    @staticmethod
    def _stage(move, target):
        return target / ('.x2lt-library-move-' + move['token'])

    def _copy(self, move, source, target):
        identifier = move['id']
        files = self._files(identifier)
        stage = self._stage(move, target)
        stage.mkdir(exist_ok=True)
        if not stat.S_ISDIR(_info(stage).st_mode):
            raise ValueError('迁移暂存目录已替换')
        temporary = stage / 'payload'
        self._discard_temporary(temporary)
        self._check_space(target, sum(row['size'] for row in files if not row['directory']
                                      and not self._existing_checkpoint(row, target / row['relative'])))
        receipts = self._receipts(source)
        for row in files:
            self._check_cancel()
            relative = row['relative']
            before, after = source / relative, target / relative
            if _signature(_info(before)) != row['signature']:
                raise ValueError(f'原文件在迁移期间已变更：{before}')
            if row['directory']:
                after.mkdir(exist_ok=True)
                if not stat.S_ISDIR(_info(after).st_mode):
                    raise ValueError(f'目标目录已替换：{after}')
                continue
            if not self._existing_checkpoint(row, after):
                old_digest, new_digest = self._copy_file(before, temporary, row['signature'],
                                                        receipts.get(Path(relative)), source, target)
                self._write('''UPDATE library_move_files SET original_hash=?,target_hash=?
                               WHERE library=? AND relative=?''', (old_digest, new_digest, identifier, relative))
                self._publish(temporary, after)
            if not row['published']:
                with self.store.lock, self.store.db:
                    self.store.db.execute('UPDATE library_move_files SET published=1 WHERE library=? AND relative=?',
                                          (identifier, relative))
                    self.store.db.execute('''UPDATE library_moves SET copied_files=copied_files+1,
                        copied_bytes=copied_bytes+? WHERE id=?''', (row['size'], identifier))

        expected = {row['relative']: row['signature'] for row in files}
        current = {relative: signature for relative, _, signature, _ in self._inventory(source, cancellable=True)}
        if current != expected:
            raise ValueError('原媒体库在迁移期间增删或修改了内容')
        for row in self._files(identifier):
            destination = target / row['relative']
            if row['directory']:
                self._check_cancel()
                if not stat.S_ISDIR(_info(destination).st_mode):
                    raise ValueError(f'目标目录已替换：{destination}')
                if any(str(path.relative_to(target)) not in expected for path in destination.iterdir()):
                    raise ValueError(f'目标目录出现了额外文件：{destination}')
            else:
                self._verify(destination, row['target_hash'])
        self._roots(move)
        stage.rmdir()

    @staticmethod
    def _existing_checkpoint(row, path):
        try:
            _info(path)
        except FileNotFoundError:
            return False
        if not row['target_hash']:
            raise ValueError(f'迁移目标目录出现同名文件：{path}')
        return True

    @staticmethod
    def _discard_temporary(path):
        try:
            info = _info(path)
        except FileNotFoundError:
            return
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('迁移暂存文件已替换')
        path.unlink()

    def _hash(self, path, expected=None):
        self._check_cancel()
        before = _signature(_info(path))
        if expected is not None and before != expected:
            raise ValueError(f'文件在迁移期间已变更：{path}')
        digest = sha256()
        with path.open('rb') as handle:
            if _signature(os.fstat(handle.fileno())) != before:
                raise ValueError(f'文件在迁移期间已变更：{path}')
            while chunk := handle.read(BLOCK):
                self._check_cancel()
                digest.update(chunk)
            if _signature(os.fstat(handle.fileno())) != before:
                raise ValueError(f'文件在迁移期间已变更：{path}')
        if _signature(_info(path)) != before:
            raise ValueError(f'文件在迁移期间已变更：{path}')
        return digest.hexdigest()

    def _verify(self, path, digest, signature=None):
        if self._hash(path, signature) != digest:
            raise ValueError(f'迁移文件校验不一致：{path}')

    def _receipts(self, source):
        receipts = {}
        for row in self._rows('SELECT * FROM jobs WHERE output=?', (str(source),)):
            try:
                path = comparable_path(manifest(original_path(row))).relative_to(source)
            except (ValueError, OSError):
                continue
            receipts[path] = row
        return receipts

    @staticmethod
    def _receipt_bytes(raw, row, source, target):
        try:
            receipt = json.loads(raw.decode('utf8'))
        except (UnicodeDecodeError, ValueError):
            return raw
        if (not isinstance(receipt, dict) or receipt.get('downloaded') is not True
                or not isinstance(receipt.get('archive_output'), str)
                or comparable_path(Path(receipt['archive_output'])) != source
                or any(receipt.get(key) != row[key] for key in ('tweet_id', 'media_id', 'quality'))):
            return raw
        receipt['archive_output'] = str(target)
        for key in ('local_path', 'pending_source', 'pending_target'):
            if isinstance(receipt.get(key), str):
                receipt[key] = _remap(receipt[key], source, target)
        return json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf8')

    def _copy_file(self, path, temporary, signature, receipt, source, target):
        info = _info(path)
        original, written = sha256(), sha256()
        with path.open('rb') as input_file, temporary.open('xb') as output:
            if _signature(os.fstat(input_file.fileno())) != signature:
                raise ValueError(f'原文件在迁移期间已变更：{path}')
            if receipt and info.st_size <= BLOCK:
                contents = input_file.read(BLOCK + 1)
                updated = self._receipt_bytes(contents, receipt, source, target)
                original.update(contents)
                written.update(updated)
                output.write(updated)
            else:
                while chunk := input_file.read(BLOCK):
                    self._check_cancel()
                    original.update(chunk)
                    written.update(chunk)
                    output.write(chunk)
            if (_signature(os.fstat(input_file.fileno())) != signature
                    or _signature(_info(path)) != signature):
                raise ValueError(f'原文件在迁移期间已变更：{path}')
            output.flush()
            os.fsync(output.fileno())
        os.utime(temporary, ns=(info.st_atime_ns, info.st_mtime_ns))
        return original.hexdigest(), written.hexdigest()

    @staticmethod
    def _publish(temporary, target):
        if not stat.S_ISDIR(_info(target.parent).st_mode):
            raise ValueError(f'迁移目标上级目录已替换：{target.parent}')
        if os.name == 'nt':
            # Windows rename fails if the destination has appeared.
            temporary.rename(target)
        else:
            os.link(temporary, target)
            temporary.unlink()

    def _has_table(self, table):
        return bool(self._rows("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))

    def _local_moves(self, source, target):
        if not self._has_table('local_locations'):
            return []
        moved = []
        for row in self._rows('SELECT * FROM local_locations'):
            relative = _path_under(row['path'], source)
            if relative is None or row['path_key'] != path_key(row['path']):
                continue
            path = target / relative
            root = Path(_remap(row['root'], source, target))
            if not path.is_relative_to(root):
                root = target
            moved.append((row, relative, path, root))
        return moved

    def _check_catalog(self, identifier, source, target, *, preflight=False):
        with self.store.lock:
            if self._rows('SELECT 1 FROM jobs WHERE output=? LIMIT 1', (str(target),)) or self._rows(
                    'SELECT 1 FROM model_locations WHERE output=? LIMIT 1', (str(target),)):
                raise ValueError('目标路径已有独立下载记录，不能合并')
            if self._has_table('auto_flatten_moves') and self._rows(
                    'SELECT 1 FROM auto_flatten_moves WHERE library=? LIMIT 1', (identifier,)):
                raise ValueError('请先完成原有的脱壳整理')
            if self._rows('''SELECT 1 FROM removal_items r JOIN jobs j ON j.id=r.job_id
                             WHERE r.status='pending' AND j.output=? LIMIT 1''', (str(source),)):
                raise ValueError('请先完成该媒体库的文件移除')
            for row, relative, path, _ in self._local_moves(source, target):
                if preflight and relative == Path('.') and row['directory'] and any(target.iterdir()):
                    raise ValueError('原媒体库目录已作为媒体入库，请选择空目标目录')
                if self._rows('SELECT 1 FROM local_locations WHERE path_key=? AND id!=?', (path_key(path), row['id'])):
                    raise ValueError(f'目标路径已有本地媒体记录：{path}')

    def _bind(self, move, source, target):
        self._check_cancel()
        identifier = move['id']
        old, new = str(source), str(target)
        inventory = {row['relative']: row['signature'] for row in self._files(identifier)}
        with self.store.lock, self.store.db:
            db = self.store.db
            self._check_catalog(identifier, source, target)
            self.manager.validate_migration_target(identifier, target)
            for row, relative, path, root in self._local_moves(source, target):
                signature = row['sig']
                if relative == Path('.') or str(relative) in inventory:
                    info = _info(path)
                    if ((row['directory'] and stat.S_ISDIR(info.st_mode))
                            or inventory.get(str(relative)) == signature):
                        signature = json.dumps([info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns])
                db.execute('UPDATE local_locations SET path=?,path_key=?,root=?,sig=? WHERE id=?',
                           (str(path), path_key(path), str(root), signature, row['id']))

            for row in self._rows('SELECT id,output,local_path,relocation_source FROM jobs'):
                output = new if Path(row['output']) == source else row['output']
                local = _remap(row['local_path'], source, target)
                relocation = _remap(row['relocation_source'], source, target)
                if (output, local, relocation) != (row['output'], row['local_path'], row['relocation_source']):
                    # Existing triggers update matching model locations in the same transaction.
                    db.execute('UPDATE jobs SET output=?,local_path=?,relocation_source=? WHERE id=?',
                               (output, local, relocation, row['id']))

            for row in self._rows('SELECT id,output,local_path FROM model_locations WHERE legacy_job_id IS NULL'):
                output = new if Path(row['output']) == source else row['output']
                local = _remap(row['local_path'], source, target)
                if (output, local) != (row['output'], row['local_path']):
                    db.execute('UPDATE model_locations SET output=?,local_path=? WHERE id=?', (output, local, row['id']))

            if self._has_table('telegram_downloads'):
                db.execute('UPDATE telegram_downloads SET root=? WHERE library_id=? AND root=?', (new, identifier, old))
            for table, key in (('cloud_released', 'location'), ('cloud_download_receipts', 'job'),
                               ('removal_promoted', 'rowid')):
                if not self._has_table(table):
                    continue
                for row in self._rows(f'SELECT {key} AS move_key,path FROM {table}'):
                    path = _remap(row['path'], source, target)
                    if path != row['path']:
                        db.execute(f'UPDATE {table} SET path=? WHERE {key}=?', (path, row['move_key']))
            db.execute("UPDATE library_moves SET phase='publish' WHERE id=?", (identifier,))

    def _publish_root(self, identifier, source, target):
        self.manager.rebind_migration(identifier, source, target)
        if self.settings:
            self.settings.rebind_output(source, target)

    def _clean(self, move, source, target):
        files = self._files(move['id'])
        # Validate all surviving directory identities before deleting regular files.
        for row in files:
            if row['directory']:
                self._check_cancel()
                self._check_original_directory(source / row['relative'], row['signature'])
        for row in files:
            self._check_cancel()
            if row['directory']:
                continue
            before, after = source / row['relative'], target / row['relative']
            self._verify(after, row['target_hash'])
            try:
                _info(before)
            except FileNotFoundError:
                continue
            self._verify(before, row['original_hash'], row['signature'])
            if _signature(_info(before)) != row['signature']:
                raise ValueError(f'原文件在迁移期间已变更：{before}')
            before.unlink()
        for row in sorted((row for row in files if row['directory']),
                          key=lambda row: len(Path(row['relative']).parts), reverse=True):
            self._check_cancel()
            folder = source / row['relative']
            if self._check_original_directory(folder, row['signature']):
                destination = target / row['relative']
                if not stat.S_ISDIR(_info(destination).st_mode):
                    raise ValueError(f'目标目录已替换：{destination}')
                folder.rmdir()

    @staticmethod
    def _check_original_directory(path, signature):
        try:
            info = _info(path)
        except FileNotFoundError:
            return False
        if not stat.S_ISDIR(info.st_mode) or _identity(info) != signature:
            raise ValueError(f'原目录在迁移期间已变更：{path}')
        return True
