# SPDX-License-Identifier: GPL-2.0-only
"""Persistent Telegram media downloads with verified, resumable staging."""
from contextlib import closing, nullcontext
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import stat
import threading
import time
from uuid import uuid4

from ..cloud_catalog import safe_filename
from ..auto_flatten import AutoFlatten, TELEGRAM_PLACEMENT_ERROR
from ..images import check_image, is_alias
from ..local_import_files import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS, selected_path
from ..transfer import Paused, file_hash
from .telegram_transport import TelegramTransportError


_FIELDS = ('peer_id', 'peer_title', 'message_id', 'media_id', 'group_id', 'title',
           'body', 'url', 'kind', 'extension', 'size', 'width', 'height', 'name',
           'sent_at', 'forward_id', 'forward_title', 'forward_url', 'avatar_key')
TASK_CARD_FIELDS = ('body', 'sent_at', 'forward_title', 'forward_id', 'avatar_key', 'extension', 'width', 'height')
_IDENTITY_FIELDS = ('peer_id', 'message_id', 'media_id')
_CHECKPOINT_BYTES = 4 * 1024**2
_CONTROLS = {
    'pause': ('paused', ('queued', 'downloading', 'waiting', 'deferred')),
    'resume': ('queued', ('paused',)),
    'cancel': ('canceled', ('queued', 'downloading', 'waiting', 'deferred', 'paused', 'failed')),
    'retry': ('queued', ('failed', 'canceled')),
    'delete': ('canceled', ('queued', 'downloading', 'waiting', 'deferred', 'paused', 'failed', 'completed')),
}


class _DownloadError(ValueError):
    """A download failure whose message is safe to show in the desktop app."""


def task_card_keys(item):
    message_key = f"{item['peer_id']}:{item['message_id']}"
    group = item.get('group_id')
    card_key = f"{item['peer_id']}:post:{group}" if group else message_key
    return dict(message_key=message_key, media_key=f"{message_key}:{item['media_id']}", card_key=card_key)


class TelegramQueue:
    def __init__(self, store, libraries, transport, register, notify, media_limit=lambda: 0,
                 task_limit=lambda: 0):
        self.store = store
        self.libraries = libraries
        self.transport = transport
        self.register = register
        self.notify = notify
        self.media_limit = media_limit
        self.task_limit = task_limit
        self.condition = threading.Condition()
        self.worker = None
        self.active = None
        self.cancel = None
        self.delete_active_partial = False
        self.received = 0
        self.speed = 0
        self.stopping = False
        self.window_paused = False
        with store.lock, store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS telegram_queue_hold (
                id INTEGER PRIMARY KEY CHECK(id=1)
            )""")
            self.manual_hold = bool(store.db.execute('SELECT 1 FROM telegram_queue_hold').fetchone())
            store.db.execute("""
                CREATE TABLE IF NOT EXISTS telegram_downloads (
                    id TEXT PRIMARY KEY, library_id TEXT NOT NULL,
                    peer_id TEXT NOT NULL, message_id TEXT NOT NULL, media_id TEXT NOT NULL,
                    payload TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
                    root TEXT NOT NULL DEFAULT '', filename TEXT NOT NULL,
                    total INTEGER NOT NULL, received INTEGER NOT NULL DEFAULT 0,
                    partial_digest TEXT NOT NULL DEFAULT '', digest TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '', retry_at REAL NOT NULL DEFAULT 0,
                    UNIQUE (library_id, peer_id, message_id, media_id)
                )
            """)
            columns = {row[1] for row in store.db.execute('PRAGMA table_info(telegram_downloads)')}
            if 'auto_paused' not in columns:
                store.db.execute('ALTER TABLE telegram_downloads ADD COLUMN auto_paused INTEGER NOT NULL DEFAULT 0')
            if 'background' not in columns:
                store.db.execute('ALTER TABLE telegram_downloads ADD COLUMN background INTEGER NOT NULL DEFAULT 0')
            store.db.execute("""
                UPDATE telegram_downloads SET state='paused', auto_paused=1, retry_at=0
                WHERE state IN ('queued', 'downloading', 'waiting')
            """)
            if 'hidden' not in columns:
                store.db.execute('ALTER TABLE telegram_downloads ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0')
            store.db.execute('CREATE INDEX IF NOT EXISTS telegram_download_state ON telegram_downloads(state)')

    def _rows(self, sql, args=()):
        with self.store.lock:
            return [dict(row) for row in self.store.db.execute(sql, args)]

    def _write(self, sql, args=()):
        with self.store.lock, self.store.db:
            return self.store.db.execute(sql, args).rowcount

    @staticmethod
    def _payload(item):
        payload = {field: item.get(field) for field in _FIELDS}
        if not all(payload[field] not in (None, '') for field in _IDENTITY_FIELDS):
            raise ValueError('Telegram 媒体缺少来源标识')
        payload['size'] = int(payload['size'] or 0)
        if not 0 <= payload['size'] < 2**63:
            raise ValueError('Telegram 媒体大小无效')
        extension = str(payload['extension'] or '').lower().lstrip('.')
        if payload['kind'] == 'image' and extension in IMAGE_EXTENSIONS:
            return payload, extension
        if payload['kind'] == 'video' and extension in VIDEO_EXTENSIONS:
            return payload, extension
        raise ValueError('Telegram 媒体格式不受支持')

    def add(self, items, library_id, *, revive_canceled=True, retry_failed=False, background=False):
        library = self.libraries.get(library_id)
        records = []
        for item in items:
            payload, extension = self._payload(item)
            identifier = uuid4().hex
            name = safe_filename(str(payload['name'] or payload['title'] or 'Telegram'), extension)
            # The unique suffix gives staging and final files an unambiguous owner.
            filename = f'{identifier}-{Path(name).stem[:90]}.{extension}'
            records.append((identifier, library['id'], str(payload['peer_id']), str(payload['message_id']),
                            str(payload['media_id']), json.dumps(payload, ensure_ascii=False),
                            filename, payload['size']))
        with self.condition:
            if self.stopping:
                raise ValueError('Telegram 下载队列正在关闭')
            held = self.manual_hold or self.window_paused
            state = 'paused' if held else 'deferred' if self.task_limit() else 'queued'
            with self.store.lock, self.store.db:
                count = self.store.db.executemany("""
                    INSERT INTO telegram_downloads
                    (id, library_id, peer_id, message_id, media_id, payload, filename, total, state, auto_paused, background)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(library_id,peer_id,message_id,media_id) DO UPDATE SET
                        state=excluded.state, auto_paused=excluded.auto_paused, background=excluded.background,
                        hidden=0, retry_at=0, error=''
                    WHERE telegram_downloads.id IS NOT ? AND (
                        (? AND telegram_downloads.state='canceled') OR
                        (? AND telegram_downloads.state='failed' AND telegram_downloads.error!='超过单个媒体体积上限')
                    )
                """, [(*record, state, self.window_paused and not self.manual_hold, background, self.active,
                       revive_canceled, retry_failed)
                       for record in records]).rowcount
            if count and not held:
                self._wake()
        if count:
            self.notify()
        return count

    def list(self, *, visible_only=False):
        with self.condition:
            result = []
            where = "WHERE hidden=0 AND state!='canceled'" if visible_only else ''
            for row in self._rows(f'SELECT * FROM telegram_downloads {where} ORDER BY rowid DESC'):
                item = json.loads(row['payload'])
                live = self.active == row['id'] and row['state'] == 'downloading'
                result.append(dict(id=row['id'], title=item.get('title') or item.get('name') or 'Telegram 媒体',
                                   kind=item['kind'], state=row['state'], error=row['error'], total=row['total'],
                                   received=self.received if live else row['received'],
                                   speed=self.speed if live else 0,
                                   library_id=row['library_id'], **task_card_keys({**row, 'group_id': item.get('group_id')}),
                                   **{key: item[key] for key in TASK_CARD_FIELDS if key in item}))
            return result

    def preview_job(self, identifier):
        rows = self._rows("""SELECT library_id, peer_id, message_id, media_id, payload, state, digest
                             FROM telegram_downloads WHERE id=?""", (identifier,))
        if not rows:
            return None
        job = rows[0]
        item = json.loads(job['payload'])
        item.update({key: job[key] for key in _IDENTITY_FIELDS})
        return job, item

    def states(self, library, peer, *, include_canceled=False):
        return {(row['message_id'], row['media_id']): row['state'] for row in self._rows(
            """SELECT message_id,media_id,state FROM telegram_downloads
               WHERE library_id=? AND peer_id=? AND (? OR state!='canceled')""",
            (library, peer, include_canceled))}

    def avatar_item(self, key):
        rows = self._rows("""SELECT payload FROM telegram_downloads
            WHERE json_extract(payload,'$.avatar_key')=? ORDER BY rowid DESC LIMIT 1""", (key,))
        return json.loads(rows[0]['payload']) if rows else None

    def busy(self):
        with self.condition:
            return self.active is not None or bool(self._rows("""
                SELECT 1 FROM telegram_downloads WHERE state IN ('queued', 'waiting') LIMIT 1
            """))

    def control(self, action, ids=None, *, delete_partial=True, report=False):
        if action not in _CONTROLS:
            raise ValueError('Telegram 下载操作无效')
        target, previous = _CONTROLS[action]
        if target == 'queued' and self.task_limit():
            target = 'deferred'
        ids = None if ids is None else set(ids)
        changed = []
        with self.condition:
            if self.stopping:
                return [] if report else False
            rows = self._rows('SELECT * FROM telegram_downloads')
            for row in rows:
                identifier = row['id']
                pending_pause = action == 'pause' and row['state'] == 'paused' and row['auto_paused']
                if row['hidden'] or (row['state'] not in previous and not pending_pause) or ids is not None and identifier not in ids:
                    continue
                if action == 'delete' and row['state'] == 'completed':
                    self._write('UPDATE telegram_downloads SET hidden=1 WHERE id=?', (identifier,))
                    changed.append(row)
                    continue
                if row['state'] == 'canceled' and identifier == self.active:
                    continue
                self._write("""
                    UPDATE telegram_downloads SET state=?, auto_paused=0, error='', retry_at=0 WHERE id=?
                """, (target, identifier))
                if target == 'canceled':
                    if identifier == self.active:
                        self.delete_active_partial = delete_partial
                    elif delete_partial:
                        self._discard_partial(row)
                if identifier == self.active and target in ('paused', 'canceled'):
                    self.cancel.set()
                changed.append(row)
            if changed:
                self._wake()
        if changed:
            self.notify()
        if report:
            return [dict(library_id=row['library_id'], media_key=task_card_keys(row)['media_key'])
                    for row in changed]
        return bool(changed)

    def _wake(self):
        if self.worker is None:
            self.worker = threading.Thread(target=self._run, name='x2lt-telegram', daemon=True)
            self.worker.start()
        self.condition.notify_all()

    def set_download_window(self, enabled, opened):
        with self.condition:
            self.window_paused = enabled and not opened
            if not self.window_paused:
                return
            changed = self._write("""UPDATE telegram_downloads SET state='paused', auto_paused=1, retry_at=0
                                     WHERE state IN ('queued','waiting','downloading','deferred')""")
            if changed and self.cancel is not None:
                self.cancel.set()
        if changed:
            self.notify()

    def resume_scheduled(self, *, background_only=False):
        with self.condition:
            if self.stopping or self.window_paused or self.manual_hold:
                return
            state = 'deferred' if self.task_limit() else 'queued'
            changed = self._write("""UPDATE telegram_downloads SET state=?, auto_paused=0
                                     WHERE state='paused' AND auto_paused=1 AND hidden=0 AND (?=0 OR background=1)""",
                                  (state, background_only))
            if changed or self._rows("SELECT 1 FROM telegram_downloads WHERE state='deferred' LIMIT 1"):
                self._wake()
        if changed:
            self.notify()

    def set_manual_hold(self, held, *, report=False):
        with self.condition:
            self.manual_hold = held
            if held:
                self._write('INSERT OR IGNORE INTO telegram_queue_hold VALUES (1)')
                return self.control('pause', report=report)
            self._write('DELETE FROM telegram_queue_hold')
            return self.control('resume', report=report)

    def close(self):
        with self.condition:
            if not self.stopping:
                self.stopping = True
                self._write("""UPDATE telegram_downloads SET state='paused', auto_paused=1, retry_at=0
                               WHERE state IN ('queued', 'waiting', 'downloading', 'deferred')""")
            if self.cancel is not None:
                self.cancel.set()
            self.condition.notify_all()
            worker = self.worker
        if worker is not None:
            worker.join(timeout=.1)
        return worker is None or not worker.is_alive()

    def _run(self):
        while True:
            with self.condition:
                if self.stopping:
                    self.worker = None
                    return
                self._admit()
                now = time.time()
                rows = self._rows("""
                    SELECT * FROM telegram_downloads WHERE state='queued'
                       OR (state='waiting' AND retry_at<=?) ORDER BY rowid LIMIT 1
                """, (now,))
                if self.stopping or not rows:
                    waits = [] if self.stopping else self._rows("""
                        SELECT retry_at FROM telegram_downloads WHERE state='waiting' ORDER BY retry_at LIMIT 1
                    """)
                    if not waits:
                        self.worker = None
                        return
                    self.condition.wait(max(0, min(60, waits[0]['retry_at'] - time.time())))
                    continue
                row = rows[0]
                identifier = row['id']
                self.active = identifier
                self.cancel = threading.Event()
                self.received, self.speed = row['received'], 0
                self._write("UPDATE telegram_downloads SET state='downloading', error='' WHERE id=?", (identifier,))
            self.notify()
            try:
                self._download(row)
            except Paused as exc:
                self._settle(identifier, 'paused', str(exc))
            except _DownloadError as exc:
                self._settle(identifier, 'failed', str(exc))
            except Exception as exc:
                seconds = getattr(exc, 'seconds', None)
                if isinstance(seconds, (int, float)) and 0 < seconds < math.inf:
                    self._settle(identifier, 'waiting', 'Telegram 限流，等待后重试', time.time() + seconds)
                elif isinstance(exc, TelegramTransportError):
                    self._settle(identifier, 'failed', str(exc))
                else:
                    self._settle(identifier, 'failed', 'Telegram 下载或入库失败，请检查连接和媒体库后重试')
            finally:
                with self.condition:
                    if self.delete_active_partial:
                        canceled = self._rows("SELECT * FROM telegram_downloads WHERE id=? AND state='canceled'", (identifier,))
                        if canceled:
                            self._discard_partial(canceled[0])
                    self.delete_active_partial = False
                    self.active = self.cancel = None
                    self.received = self.speed = 0
                self.notify()

    def _admit(self):
        limit = self.task_limit()
        with self.store.lock, self.store.db as db:
            slots = 500
            if limit:
                active = db.execute("""SELECT count(*) FROM telegram_downloads
                                       WHERE state IN ('queued','downloading','waiting')""").fetchone()[0]
                slots = min(500, max(0, limit - active))
            if slots:
                db.execute("""UPDATE telegram_downloads SET state='queued' WHERE id IN (
                    SELECT id FROM telegram_downloads WHERE state='deferred' ORDER BY rowid LIMIT ?)""",
                           (slots,))

    def _settle(self, identifier, state, error, retry_at=0):
        with self.condition:
            self._write("""
                UPDATE telegram_downloads SET state=?, error=?, retry_at=?
                WHERE id=? AND state='downloading'
            """, (state, error, retry_at, identifier))

    def _check_cancel(self):
        if self.cancel.is_set():
            raise Paused('下载已暂停，进度已保留')

    def _paths(self, row, create=False):
        library = self.libraries.get(row['library_id'])
        try:
            root = selected_path(library['path'])
        except (OSError, ValueError) as exc:
            raise Paused('媒体库目录不可用，请恢复后继续') from exc
        if not root.is_dir():
            raise Paused('媒体库目录不可用，请恢复后继续')
        if row['root'] and root != Path(row['root']):
            raise Paused('媒体库路径已更改，请恢复原路径后继续')
        directory = root / 'Telegram'
        if is_alias(directory):
            raise _DownloadError('Telegram 下载目录不能为链接或目录联接')
        if create:
            directory.mkdir(exist_ok=True)
        if not directory.is_dir() or selected_path(directory) != directory:
            raise _DownloadError('Telegram 下载目录不可用')
        filename = row['filename']
        if Path(filename).name != filename or not filename.startswith(row['id'] + '-'):
            raise _DownloadError('Telegram 下载文件名无效')
        final = directory / filename
        return root, final, final.with_suffix(final.suffix + '.part')

    @staticmethod
    def _artifact(path):
        if is_alias(path):
            raise _DownloadError('Telegram 下载文件不能为链接或目录联接')
        try:
            info = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(info.st_mode):
            raise _DownloadError('Telegram 下载路径存在非普通文件')
        return info

    def _discard_partial(self, row):
        if not row['root']:
            return
        try:
            _, _, part = self._paths(row)
            info = self._artifact(part)
            if info and info.st_nlink == 1:
                part.unlink()
        except (OSError, ValueError, Paused):
            return
        self._write("""
            UPDATE telegram_downloads SET received=0, partial_digest='' WHERE id=?
        """, (row['id'],))
        self.libraries.invalidate_capacity(row['library_id'])

    def _download(self, row):
        item = json.loads(row['payload'])
        identifier = row['id']
        limit = self.media_limit()
        if limit and max(row['total'], row['received']) > limit:
            raise _DownloadError('超过单个媒体体积上限')
        root, final, part = self._paths(row, create=True)
        with self.libraries.lease(part) as lease:
            if lease.library_id != row['library_id']:
                raise Paused('目标媒体库已更改，请恢复后继续')
            self._check_cancel()
            self._write('UPDATE telegram_downloads SET root=? WHERE id=?', (str(root), identifier))
            existing_final = self._artifact(final)
            if row['digest'] and existing_final:
                if final.stat().st_size != row['total'] or file_hash(final, self.cancel) != row['digest']:
                    raise _DownloadError('已保存的 Telegram 文件与校验记录不符')
                size, digest = row['total'], row['digest']
                promote = False
            else:
                if existing_final:
                    raise _DownloadError('Telegram 目标文件已存在')
                if row['digest'] and self._artifact(part) is None:
                    self._write("""
                        UPDATE telegram_downloads SET received=0, partial_digest='', digest='' WHERE id=?
                    """, (identifier,))
                    row = {**row, 'received': 0, 'partial_digest': '', 'digest': ''}
                size, digest = self._receive(row, item, part, lease, limit)
                promote = True
                if item['kind'] == 'image':
                    try:
                        check_image(part, 'jpg' if final.suffix == '.jpeg' else final.suffix[1:])
                    except ValueError as exc:
                        raise _DownloadError('Telegram 图片无法解码或格式无效') from exc
            with self.condition:
                self._check_cancel()
                if promote:
                    self._write("UPDATE telegram_downloads SET digest=?, total=? WHERE id=?", (digest, size, identifier))
                    self._promote(part, final)
                elif self._same_file(part, final):
                    part.unlink()
                self.register({**item, 'id': identifier, 'library_id': row['library_id']}, final, digest)
                self._write("""
                    UPDATE telegram_downloads SET state='completed', received=?, error='', retry_at=0 WHERE id=?
                """, (size, identifier))
            library = self.libraries.get(row['library_id'])
            if library.get('auto_flatten'):
                try:
                    AutoFlatten(self.store).place_download(library, 'telegram', identifier, self.cancel,
                                                           lambda event: self.notify())
                except Exception:
                    self._write("UPDATE telegram_downloads SET error=? WHERE id=?",
                                (TELEGRAM_PLACEMENT_ERROR, identifier))

    @staticmethod
    def _same_file(part, final):
        first, second = TelegramQueue._artifact(part), TelegramQueue._artifact(final)
        return first and second and os.path.samestat(first, second)

    @staticmethod
    def _promote(part, final):
        if not TelegramQueue._artifact(part):
            raise _DownloadError('Telegram 暂存文件不存在')
        if os.name == 'nt':
            # Windows rename refuses to overwrite an existing destination.
            part.rename(final)
        else:
            # Unix rename can replace a file that arrived after the existence check.
            os.link(part, final)
            part.unlink()

    def _receive(self, row, item, part, lease, limit=0):
        offset, total = row['received'], row['total']
        previous = self._artifact(part)
        if previous and previous.st_nlink != 1:
            raise _DownloadError('Telegram 暂存文件不能为硬链接')
        if offset and (previous is None or previous.st_size < offset):
            raise _DownloadError('Telegram 暂存文件缺失或不完整，请取消后重试')
        if total:
            lease.reserve(total - offset)
        digest = sha256()
        start = last_report = time.monotonic()
        received = saved = offset
        with part.open('r+b' if previous else 'xb') as output:
            if previous and not os.path.samestat(previous, os.fstat(output.fileno())):
                raise _DownloadError('Telegram 暂存文件已被替换')
            while output.tell() < offset:
                self._check_cancel()
                chunk = output.read(min(_CHECKPOINT_BYTES, offset - output.tell()))
                if not chunk:
                    raise _DownloadError('Telegram 暂存文件无法校验')
                digest.update(chunk)
            if offset and digest.hexdigest() != row['partial_digest']:
                raise _DownloadError('Telegram 暂存文件校验失败，请取消后重试')
            output.truncate(offset)
            try:
                self._check_cancel()
                if not total or offset < total:
                    chunks = iter(self.transport.chunks(item, offset))
                    with closing(chunks) if hasattr(chunks, 'close') else nullcontext():
                        for chunk in chunks:
                            self._check_cancel()
                            if not chunk:
                                continue
                            if limit and received + len(chunk) > limit:
                                raise _DownloadError('超过单个媒体体积上限')
                            if total and received + len(chunk) > total:
                                raise _DownloadError('Telegram 下载长度超过消息记录')
                            if total:
                                output.write(chunk)
                            else:
                                # Recheck space for each additional chunk when metadata has no length.
                                with self.libraries.lease(part) as chunk_space:
                                    chunk_space.reserve(len(chunk))
                                    output.write(chunk)
                                    output.flush()
                            digest.update(chunk)
                            received += len(chunk)
                            if received - saved >= _CHECKPOINT_BYTES:
                                self._checkpoint(row['id'], output, received, digest)
                                saved = received
                            now = time.monotonic()
                            report = now - last_report >= .25
                            with self.condition:
                                self.received = received
                                if report:
                                    self.speed = round((received - offset) / (now - start))
                            if report:
                                self.notify()
                                last_report = now
                            self._check_cancel()
            finally:
                if received != saved:
                    self._checkpoint(row['id'], output, received, digest)
        if not received or total and received != total:
            raise _DownloadError('Telegram 下载长度与消息记录不符')
        self._check_cancel()
        actual = self._artifact(part)
        checksum = digest.hexdigest()
        if actual is None or actual.st_size != received or file_hash(part, self.cancel) != checksum:
            raise _DownloadError('Telegram 暂存文件最终校验失败')
        return received, checksum

    def _checkpoint(self, identifier, output, received, digest):
        output.flush()
        os.fsync(output.fileno())
        self._write('UPDATE telegram_downloads SET received=?, partial_digest=? WHERE id=?',
                    (received, digest.hexdigest(), identifier))
