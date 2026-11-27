# SPDX-License-Identifier: GPL-2.0-only
"""Verified in-library placement of downloaded X and Telegram media."""
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from .archive import comparable_path, manifest, original_path, read_json, save_receipt, valid_receipt
from .images import is_alias
from .local_collection import file_signature, path_key
from .local_import_files import IMAGE_EXTENSIONS, VIDEO_EXTENSIONS
from .security import redact
from .transfer import Paused, file_hash


CONCENTRATION = '集中媒体'
TELEGRAM_PLACEMENT_ERROR = '下载已完成，自动集中待重试'


class AutoFlatten:
    def __init__(self, store):
        self.store = store
        self.db = store.db
        with store.lock, self.db:
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS auto_flatten_moves(
                    library TEXT NOT NULL, platform TEXT NOT NULL, job TEXT NOT NULL,
                    source TEXT NOT NULL, target TEXT NOT NULL, bytes INTEGER NOT NULL,
                    digest TEXT NOT NULL, PRIMARY KEY(library, platform, job), UNIQUE(library, target)
                )
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS auto_flatten_telegram_origins(
                    library TEXT NOT NULL, model_location INTEGER NOT NULL,
                    path TEXT NOT NULL, PRIMARY KEY(library, model_location)
                )
            """)

    def _rows(self, sql, args=()):
        with self.store.lock:
            return [dict(row) for row in self.db.execute(sql, args)]

    def _journal(self, library, platform=None, job=None):
        scope = '' if platform is None else ' AND platform=? AND job=?'
        args = (library['id'],) if platform is None else (library['id'], platform, str(job))
        return self._rows('SELECT * FROM auto_flatten_moves WHERE library=?' + scope, args)

    def _telegram(self, library, *, queue_job=None, model_location=None):
        with self.store.lock:
            tables = self.db.execute("""
                SELECT name FROM sqlite_master WHERE type='table'
                AND name IN ('telegram_downloads','telegram_saved','local_locations','local_entries')
            """).fetchall()
            names = {row[0] for row in tables}
            if not {'telegram_saved', 'local_locations', 'local_entries'} <= names:
                return []
            if queue_job is not None and 'telegram_downloads' not in names:
                return []
            queue_columns = 'q.id AS queue_job,q.filename,' if 'telegram_downloads' in names else "NULL AS queue_job,NULL AS filename,"
            if 'telegram_downloads' in names:
                queue_join = """
                    LEFT JOIN telegram_downloads q ON q.library_id=t.library AND q.peer_id=s.scope
                         AND m.native_id=q.message_id || ':' || q.media_id
                         AND q.total=ml.bytes AND q.digest=ml.sha256
                """
            else:
                queue_join = ''
            args = [library['id'], library['path'], library['path']]
            filters = []
            if queue_job is not None:
                filters.append(' AND q.id=?')
                args.append(queue_job)
            if model_location is not None:
                filters.append(' AND ml.id=?')
                args.append(model_location)
            return self._rows(f"""
                SELECT {queue_columns} ml.id, ml.sha256 AS digest, ml.bytes, l.path,
                       l.id AS local_location, o.path AS original_relative
                FROM telegram_saved t
                JOIN model_locations ml ON ml.id=t.model_location AND ml.state='completed'
                JOIN model_variants v ON v.id=t.variant
                JOIN model_media m ON m.id=v.media_id
                JOIN model_contents c ON c.id=m.content_id
                JOIN model_sources s ON s.id=c.source_id AND s.platform='telegram'
                JOIN local_locations l ON l.id=t.local_location AND l.owner=t.library
                    AND l.managed AND NOT l.directory AND l.path=ml.local_path
                JOIN local_entries e ON e.id=l.entry AND e.digest=ml.sha256 AND e.bytes=ml.bytes
                LEFT JOIN auto_flatten_telegram_origins o ON o.library=t.library AND o.model_location=ml.id
                {queue_join}
                WHERE t.library=? AND l.root=? AND ml.output=?
                    AND ml.verification IN ('length+sha256','sha256')
                    AND NOT EXISTS (SELECT 1 FROM telegram_saved other
                                    WHERE other.local_location=l.id AND other.model_location<>ml.id)
            """ + ''.join(filters), args)

    def _locations(self, library, platform, row):
        root = Path(library['path'])
        if platform == 'x':
            original = original_path(row)
            current = comparable_path(Path(row['local_path'])) if row.get('local_path') else original
            stem = f"{row['author']}_{row['tweet_id']}_{row['media_id']}_{row['quality']}"
            return original, current, stem
        current = comparable_path(Path(row['path']))
        if row['original_relative']:
            original = root / row['original_relative']
        elif name := row['filename']:
            if Path(name).name != name or not name.startswith(row['queue_job'] + '-'):
                raise ValueError('Telegram 原始下载文件名无效')
            original = root / 'Telegram' / name
        elif current.parent == root / 'Telegram':
            original = current
        else:
            raise ValueError('无法确定 Telegram 原始下载位置')
        return original, current, original.stem

    @staticmethod
    def _eligible(root, platform, original, current, enabled):
        if not current.is_relative_to(root) or '..' in current.parts:
            return False
        concentrated = current.parent == root / CONCENTRATION
        return (not concentrated and (platform == 'x' or current == original)) if enabled else concentrated

    def _candidates(self, library, enabled):
        root = Path(library['path'])
        for platform, rows in (
            ('x', self.store.completed_for_output(root)), ('telegram', self._telegram(library)),
        ):
            for row in rows:
                try:
                    original, current, _ = self._locations(library, platform, row)
                except (OSError, ValueError):
                    continue
                if current.suffix.lower().lstrip('.') not in IMAGE_EXTENSIONS | VIDEO_EXTENSIONS:
                    continue
                if self._eligible(root, platform, original, current, enabled):
                    yield platform, row

    def needs_recovery(self, library):
        if library.get('auto_flatten_pending') or self.has_pending_move(library):
            return True
        if not library.get('auto_flatten'):
            return False
        for platform, row in self._candidates(library, True):
            try:
                if self._locations(library, platform, row)[1].is_file():
                    return True
            except OSError:
                continue
        return False

    def has_pending_move(self, library):
        return bool(self._journal(library))

    def place_download(self, library, platform, job, cancel, emit):
        if not library or not library.get('auto_flatten'):
            return
        rows = self.store.get_many([job]) if platform == 'x' else self._telegram(library, queue_job=job)
        if rows:
            result = self._place(library, platform, rows[0], True, cancel)
            if result == 'moved':
                emit({'type': 'changed'})

    def apply(self, library, enabled, cancel, emit):
        root = Path(library['path'])
        self._checked_path(root, root)
        if not root.is_dir():
            raise OSError('媒体库目录不可用')
        result = dict(moved=0, skipped=0, failed=0)
        recovery = self._journal(library)
        failed = set()
        if recovery:
            emit({'type': 'operation_progress', 'done': 0, 'total': len(recovery), 'text': '恢复中断的整理'})
        for index, move in enumerate(recovery, 1):
            try:
                self._finish(library, move, cancel)
                result['moved'] += 1
            except Paused:
                raise
            except Exception as exc:
                failed.add((move['platform'], move['job']))
                result['failed'] += 1
                emit({'type': 'message', 'text': f"整理任务 {move['job']} 未完成：{redact(exc)}"})
            finally:
                emit({'type': 'operation_progress', 'done': index, 'total': len(recovery), 'text': '恢复中断的整理'})
        rows = [(platform, row) for platform, row in self._candidates(library, enabled)
                if (platform, str(row['id'])) not in failed]
        label = '集中媒体' if enabled else '恢复下载目录'
        emit({'type': 'operation_progress', 'done': 0, 'total': len(rows), 'text': label})
        for index, (platform, row) in enumerate(rows, 1):
            if cancel.is_set():
                raise Paused('媒体整理已暂停')
            try:
                result[self._place(library, platform, row, enabled, cancel)] += 1
            except Paused:
                raise
            except Exception as exc:
                result['failed'] += 1
                emit({'type': 'message', 'text': f"整理任务 {row['id']} 未完成：{redact(exc)}"})
            finally:
                emit({'type': 'operation_progress', 'done': index, 'total': len(rows), 'text': label})
        return result

    @staticmethod
    def _checked_path(root, path):
        if ('..' in path.parts or not path.is_relative_to(root)
                or any(is_alias(parent) for parent in (path, *path.parents))):
            raise ValueError('媒体整理的路径不能超出媒体库或经过目录链接')
        return path

    @staticmethod
    def _verified(path, move, cancel):
        if cancel.is_set():
            raise Paused('媒体整理已暂停')
        return (path.is_file() and path.stat().st_size == move['bytes']
                and file_hash(path, cancel) == move['digest'])

    @staticmethod
    def _owns_x_location(row, source, receipt):
        if receipt.get('local_path') == str(source):
            proof = receipt
        else:
            if is_alias(manifest(source)):
                return False
            proof = read_json(manifest(source))
            owner = proof.get('archive_job')
            if owner is None:
                url = urlsplit(row['url'])._replace(query='', fragment='').geturl()
                if proof.get('source') != url:
                    return False
            elif owner != row['id'] or proof.get('archive_output') != row['output']:
                return False
        return proof.get('sha256') == row['sha256'] and proof.get('bytes') == row['bytes']

    def _place(self, library, platform, row, enabled, cancel):
        old_move = self._journal(library, platform, row['id'])
        if old_move:
            self._finish(library, old_move[0], cancel)
            return 'moved'
        root = Path(library['path'])
        original, source, stem = self._locations(library, platform, row)
        target_directory = self._checked_path(root, root / CONCENTRATION)
        if not self._eligible(root, platform, original, source, enabled):
            return 'skipped'
        self._checked_path(root, source)
        self._checked_path(root, original)
        if platform == 'x':
            # A manual move can leave its own independent receipt journal.
            self._checked_path(root, manifest(original))
            receipt = valid_receipt(row) or {}
            if row.get('relocation_source') or receipt.get('pending_target') or receipt.get('pending_source'):
                raise ValueError('旧整理记录尚未完成')
            if source != original and not self._owns_x_location(row, source, receipt):
                raise ValueError('无法确认旧整理媒体的下载记录')
        move = dict(library=library['id'], platform=platform, job=str(row['id']),
                    source=str(source.relative_to(root)), bytes=row['bytes'],
                    digest=row['sha256'] if platform == 'x' else row['digest'])
        if not source.exists():
            return 'skipped'
        signature = file_signature(source)
        if (not re.fullmatch(r'[0-9a-f]{64}', move['digest']) or not self._verified(source, move, cancel)
                or file_signature(source) != signature):
            raise ValueError('媒体文件与下载校验记录不一致')
        with self.store.lock, self.db:
            if platform == 'x':
                [current] = self.store.get_many([row['id']])
                if current['state'] != 'completed' or current['receipt_generation'] != row['receipt_generation']:
                    return 'skipped'
                # Keep the receipt at the original download location.
                save_receipt(row, source)
            else:
                self.db.execute("""
                    INSERT OR IGNORE INTO auto_flatten_telegram_origins(library,model_location,path)
                    VALUES(?,?,?)
                """, (library['id'], row['id'], str(original.relative_to(root))))
            if enabled:
                target = self._available(library, stem, source.suffix, target_directory)
            else:
                target = original
                if target.exists():
                    raise FileExistsError('原下载位置已有文件')
            move['target'] = str(target.relative_to(root))
            self.db.execute("""
                INSERT INTO auto_flatten_moves(library,platform,job,source,target,bytes,digest)
                VALUES(:library,:platform,:job,:source,:target,:bytes,:digest)
            """, move)
        self._finish(library, move, cancel, signature=signature)
        return 'moved'

    def _available(self, library, stem, suffix, directory):
        root = Path(library['path'])
        for serial in range(10000):
            path = directory / (stem + (f'_{serial}' if serial else '') + suffix)
            if path.exists() or is_alias(path) or manifest(path).exists() or is_alias(manifest(path)):
                continue
            if self.db.execute('SELECT 1 FROM auto_flatten_moves WHERE library=? AND target=?',
                               (library['id'], str(path.relative_to(root)))).fetchone():
                continue
            if self.db.execute('SELECT 1 FROM jobs WHERE local_path=?', (str(path),)).fetchone():
                continue
            if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='local_locations'").fetchone() and \
                    self.db.execute('SELECT 1 FROM local_locations WHERE path_key=?', (path_key(path),)).fetchone():
                continue
            return path
        raise FileExistsError('集中目录没有可用的媒体文件名')

    def _finish(self, library, move, cancel, *, signature=None):
        root = Path(library['path'])
        source = self._checked_path(root, root / move['source'])
        target = self._checked_path(root, root / move['target'])
        publish = not target.exists()
        if signature is None:
            file = source if publish else target
            signature = file_signature(file)
            if not self._verified(file, move, cancel):
                raise ValueError('找不到整理媒体或校验不符')
        with self.store.lock:
            if publish:
                if file_signature(source) != signature:
                    raise ValueError('原文件在整理期间发生变化')
                target.parent.mkdir(parents=True, exist_ok=True)
                # Publish the verified file without overwriting or duplicating its contents.
                if os.name == 'nt':
                    source.rename(target)
                else:
                    os.link(source, target)
            if file_signature(target) != signature:
                raise ValueError('集中目标文件与下载校验记录不一致')
            if source.exists():
                if not source.samefile(target):
                    raise FileExistsError('整理目标已有其他文件，双方均已保留')
                source.unlink()
            self._update_location(library, move, source, target)
            with self.db:
                self.db.execute('DELETE FROM auto_flatten_moves WHERE library=? AND platform=? AND job=?',
                                (library['id'], move['platform'], move['job']))

    def _update_location(self, library, move, source, target):
        if move['platform'] == 'x':
            with self.store.lock:
                [row] = self.store.get_many([int(move['job'])])
                _, current, _ = self._locations(library, 'x', row)
                if (row['state'] != 'completed' or row['output'] != library['path']
                        or row['sha256'] != move['digest'] or current not in (source, target)):
                    raise ValueError('X 下载状态已更改')
                self.store.relocate(row['id'], target, target)
                save_receipt(row, target, clear_pending=True)
                self.store.finish_relocation(row['id'])
            return
        with self.store.lock, self.db:
            [row] = self._telegram(library, model_location=move['job'])
            if row['digest'] != move['digest'] or Path(row['path']) not in (source, target):
                raise ValueError('Telegram 下载状态已更改')
            self.db.execute('UPDATE local_locations SET path=?,path_key=?,sig=? WHERE id=?',
                            (str(target), path_key(target), json.dumps(file_signature(target)), row['local_location']))
            self.db.execute('UPDATE model_locations SET local_path=? WHERE id=?',
                            (str(target), row['id']))
            if row['queue_job']:
                self.db.execute("UPDATE telegram_downloads SET error='' WHERE id=? AND error=?",
                                (row['queue_job'], TELEGRAM_PLACEMENT_ERROR))
