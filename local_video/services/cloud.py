# SPDX-License-Identifier: GPL-2.0-only
"""Desktop Guangyapan library, transfers and durable location tracking."""
from datetime import datetime
from collections import OrderedDict
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from uuid import UUID, uuid4

from ..catalog import normalize_tag
from ..cloud_archive import organize
from ..cloud_catalog import CloudCatalog, file_type, safe_filename
from ..cloud_directory_cache import CloudDirectoryCache
from ..images import is_alias, read_image
from ..local_import_files import selected_path
from ..guangyapan import Guangyapan, GuangyapanError
from ..guangyapan_playback import GuangyapanPlayback, DEFAULT_REDIRECT
from ..guangyapan_previews import GuangyapanPreviews
from ..archive import original_path
from ..removal import recycle_file, recycle_verified


class TransferPaused(ValueError):
    pass


class TransferCancelled(Exception):
    pass


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def archive_file_identity(path):
    stat = path.stat()
    return [str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns]


class CloudService:
    def __init__(self, data, store, manager, native, runtime, provider=None, *, upload_only=False):
        self.data, self.manager, self.native, self.runtime = Path(data), manager, native, runtime
        self.upload_only = upload_only
        self.upload_filter = " AND kind='upload'" if upload_only else ""
        self.catalog = CloudCatalog(store, upload_only=upload_only)
        self.provider = provider or Guangyapan(self.data)
        self.directories = None if upload_only else CloudDirectoryCache(self.provider.list)
        self.thumbnails = None if upload_only else GuangyapanPreviews(self.data, runtime.cancel)
        self.playback = None if upload_only else GuangyapanPlayback(self.data, lambda url: self.native.open_url(url))
        self.upload_picks = OrderedDict()
        self.media_refs = OrderedDict()
        self.archive_plans = OrderedDict()
        self.archive_lock = threading.Lock()
        self.media_lock = threading.RLock()
        self.work = self.data / 'cloud-transfers'
        self.worker = None
        self.preview_slots = threading.BoundedSemaphore(2)
        self.stop = threading.Event()
        self.account = None

    def roots(self):
        return {r['path']: r['name'] for r in self.manager.items}

    def status(self, _):
        try:
            status = self.provider.status()
        except ValueError as exc:
            return dict(available=False, logged_in=False, state='UNAVAILABLE', message=str(exc))
        if status.get('logged_in') is True:
            self.set_account(status.get('account_id'))
        elif status.get('logged_in') is False:
            self.set_account(None)
        return {**{k: v for k, v in status.items() if k != 'account_id'}, 'available': True}

    def login(self, _):
        status = self.provider.login()
        if status.get('state') == 'EXPIRED':
            status = self.provider.relogin()
        self.set_account(status.get('account_id') if status.get('logged_in') else None)
        return {**{k: v for k, v in status.items() if k != 'account_id'}, 'available': True}

    def set_account(self, account):
        self.account = account
        if self.directories is not None:
            self.directories.set_account(account)

    def require_account(self, expected=None):
        public = self.status({})
        if not public.get('logged_in') or not self.account:
            raise TransferPaused('请先连接光鸭账号')
        if expected is not None and self.account != expected:
            raise TransferPaused('请连接此文件所属的光鸭账号')
        return self.account

    def list(self, p):
        refresh = p.get('refresh', False)
        account = self.require_account() if refresh or not self.account else self.account
        result = self.directories.page(account, p.get('parent', ''), max(0, min(10000, p.get('page', 0))), refresh)
        items = []
        for item in result['items']:
            media_type = file_type(item['name']) if item['type'] == 'file' else None
            if media_type:
                item = {**item, 'thumbnail_key': self.thumbnails.register(account, item['id']),
                        'view_key': self.register_view(account, item['id'], media_type[0])}
            items.append(item)
        result = {**result, 'items': items}
        files = [item['id'] for item in items if item['type'] == 'file']
        roots = self.roots()
        if not files or not roots:
            return result
        marks = lambda values: ','.join('?' for _ in values)
        previews = self.catalog.rows(f"""
            SELECT f.remote, MAX(l.id) AS preview_id FROM cloud_files f
            JOIN model_variants v ON v.id=f.variant_id
            JOIN model_media m ON m.id=v.media_id
            JOIN model_locations l ON l.variant_id=f.variant_id
            WHERE f.account=? AND f.remote IN ({marks(files)}) AND m.media_type='image'
              AND l.state='completed' AND l.output IN ({marks(roots)})
              AND NOT EXISTS(SELECT 1 FROM cloud_released r WHERE r.location=l.id)
            GROUP BY f.remote
            """, [account, *files, *roots])
        by_remote = {row['remote']: row['preview_id'] for row in previews}
        return {**result, 'items': [
            {**item, 'preview_id': by_remote[item['id']]} if item['id'] in by_remote else item
            for item in items
        ]}

    def catalog_page(self, p):
        library = p.get('library_id')
        args = []
        scope = ''
        if library:
            self.manager.get(library)
            scope = 'WHERE EXISTS(SELECT 1 FROM cloud_links link WHERE link.content_id=m.content_id AND link.library=?)'
            args.append(library)
        else:
            scope = 'WHERE EXISTS(SELECT 1 FROM cloud_files f WHERE f.variant_id=v.id)'
        base = """ FROM model_variants v JOIN model_media m ON m.id=v.media_id
            JOIN model_contents c ON c.id=m.content_id JOIN model_sources src ON src.id=c.source_id
            LEFT JOIN model_saved s ON s.media_id=m.id """
        roots = self.roots()
        count = self.catalog.one('SELECT COUNT(DISTINCT m.id) AS n' + base + scope, args)['n']
        local_count = local_bytes = 0
        if roots:
            marks = ','.join('?' for _ in roots)
            local = self.catalog.one(
                f"""SELECT COUNT(DISTINCT v.id) AS n, COALESCE(SUM(l.bytes),0) AS bytes
                {base} JOIN model_locations l ON l.variant_id=v.id {scope}
                AND l.state='completed' AND l.output IN ({marks})
                AND NOT EXISTS(SELECT 1 FROM cloud_released r WHERE r.location=l.id)""",
                [*args, *roots])
            local_count, local_bytes = local['n'], local['bytes']
        for word in p.get('query', '')[:200].split()[:12]:
            scope += """ AND (instr(lower(c.text),lower(?)) OR instr(lower(c.author),lower(?))
                        OR EXISTS(SELECT 1 FROM cloud_files f WHERE f.variant_id=v.id
                                  AND instr(lower(f.filename),lower(?)))) """
            args.extend([word]*3)
        if p.get('tag'):
            tag, _ = normalize_tag(p['tag'])
            scope += """ AND (EXISTS(SELECT 1 FROM cloud_tags t WHERE t.content_id=c.id AND t.tag=?)
                        OR (src.platform='x' AND src.scope='' AND EXISTS(
                          SELECT 1 FROM catalog_message_tags mt WHERE mt.platform='x'
                          AND mt.message_id=c.native_id AND mt.tag=?))) """
            args.extend([tag, tag])
        total = self.catalog.one('SELECT COUNT(DISTINCT c.id) AS n' + base + scope, args)['n']
        offset = min(max(0, p.get('offset', 0)), max(0, (total-1)//40)*40)
        rows = self.catalog.rows('WITH matched AS (SELECT v.id,c.id AS content,s.seq' + base + scope +
                                 '), paged AS (SELECT content,MAX(seq) recent FROM matched GROUP BY content'
                                 ' ORDER BY recent DESC,content DESC LIMIT 40 OFFSET ?)'
                                 ' SELECT matched.id FROM paged JOIN matched USING(content)'
                                 ' ORDER BY paged.recent DESC,content DESC,matched.id DESC', [*args, offset])
        return dict(items=[self.catalog.entry(row['id'], roots, self.account, self.thumbnails.register,
                                             self.register_view) for row in rows],
                    total=total, offset=offset, page_size=40, count=count, local_count=local_count, local_bytes=local_bytes)

    def positions(self, p):
        ids = p['ids']
        if not ids:
            return {}
        roots = self.roots()
        rows = self.catalog.rows('SELECT DISTINCT legacy_job_id,variant_id FROM model_locations WHERE legacy_job_id IN (' +
                                 ','.join('?' for _ in ids) + ')', ids)
        entries = {variant: self.catalog.entry(variant, roots, self.account) for variant in {r['variant_id'] for r in rows}}
        return {str(r['legacy_job_id']): entries[r['variant_id']] for r in rows}

    def tags(self, p):
        self.catalog.set_tags(p['id'], p['tags'])
        self.runtime.emit({'type': 'changed'})
        return True

    def link(self, p):
        variant = self.catalog.variant(p['id'])
        library = self.manager.get(p['library_id'])
        sql = ('INSERT OR IGNORE INTO cloud_links(content_id,library) VALUES(?,?)' if p['linked'] else
               'DELETE FROM cloud_links WHERE content_id=? AND library=?')
        self.catalog.write(sql, (variant['content_id'], library['id']))
        self.runtime.emit({'type': 'changed'})
        return True

    def locate(self, p):
        if p['id'] < 0:
            remote = self.catalog.remote(p['id'])
            self.require_account(remote['account'])
            return {'parent': remote['parent']}
        path = self.catalog.local_path(self.catalog.local(p['id']), self.roots())
        self.native.open_file(path.parent)
        return {}

    def open(self, p):
        return self.native.open_file(self.catalog.local_path(self.catalog.local(p['id']), self.roots()))

    def check(self, p):
        remote = self.catalog.remote(p['id'])
        self.require_account(remote['account'])
        match = next((item for item in self.scan(remote['parent']) if item['id'] == remote['remote']), None)
        if not match:
            self.catalog.write("UPDATE cloud_files SET state='unconfirmed' WHERE id=?", (remote['id'],))
            raise ValueError('原目录中未找到该文件，可导入文件目前所在的目录重新核对')
        self.catalog.index(remote['account'], remote['parent'], match)
        self.runtime.emit({'type': 'changed'})
        return True

    def open_source(self, p):
        row = self.catalog.variant(p['id'])
        identifier = row['native_id']
        if row['platform'] != 'x' or not identifier.isascii() or not identifier.isdigit() or len(identifier) > 30:
            raise ValueError('此内容没有可打开的源链接')
        return self.native.open_url('https://x.com/i/status/' + identifier)

    def preview(self, p):
        if not self.preview_slots.acquire(blocking=False):
            return None
        try:
            local = self.catalog.local(p['id'])
            if self.catalog.variant(local['variant_id'])['media_type'] != 'image':
                return None
            path = self.catalog.local_path(local, self.roots())
            if path.stat().st_size > 64 * 1024 * 1024:
                return None
            with read_image(path, size=(480, 320)) as image:
                data = BytesIO()
                image.save(data, format='JPEG', quality=80)
            return 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode('ascii')
        except (OSError, ValueError):
            return None
        finally:
            self.preview_slots.release()

    def preview_credentials(self, p):
        account = self.require_account()
        try:
            return self.thumbnails.configure(p['client_id'].strip(), p['client_secret'].strip(), account, self.provider)
        except (OSError, KeyError):
            raise ValueError('无法保存光鸭预览凭证') from None

    def thumbnail(self, p):
        if not self.preview_slots.acquire(blocking=False):
            return None
        try:
            image = self.thumbnails.load(p['key'], self.account, self.provider)
            if image is None:
                return None
            with image:
                data = BytesIO()
                image.save(data, format='JPEG', quality=80)
            return 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode('ascii')
        except Exception:
            return None
        finally:
            self.preview_slots.release()

    def register_view(self, account, remote, kind):
        identity = (account, remote, kind)
        key = hashlib.sha256(json.dumps(identity).encode()).hexdigest()
        with self.media_lock:
            if key not in self.media_refs:
                self.media_refs[key] = [identity, None]
            self.media_refs.move_to_end(key)
            if len(self.media_refs) > 8192:
                self.media_refs.popitem(last=False)
        return key

    def playback_status(self, _):
        return {**self.playback.status(), 'redirect': DEFAULT_REDIRECT}

    def playback_connect(self, p):
        self.require_account()
        return self.playback.start(p['client_id'].strip(), p['redirect'].strip())

    def media_view(self, p):
        if 'local_id' in p:
            row = self.catalog.local(p['local_id'])
            if self.catalog.variant(row['variant_id'])['media_type'] != 'image':
                raise ValueError('请选择图片文件')
            path = self.catalog.local_path(row, self.roots())
            if path.stat().st_size > 64 * 1024 * 1024:
                raise ValueError('图片较大，请使用系统看图工具打开原文件')
            image = read_image(path, size=(1600, 1600))
        else:
            with self.media_lock:
                ref = self.media_refs.get(p.get('key'))
            if not ref:
                raise ValueError('请重新打开目录后选择媒体')
            (account, remote, kind), previous = ref
            if kind == 'video':
                self.require_account(account)
                url, request = self.playback.video(remote, account, self.provider,
                                                   previous if p.get('retry') else None)
                with self.media_lock:
                    ref[1] = request
                return {'kind': 'video', 'url': url}
            key = self.thumbnails.register(account, remote)
            image = self.thumbnails.load(key, self.account, self.provider, large=True) if key else None
        if image is None:
            raise ValueError('光鸭没有提供此图片的预览，可保存到本地后打开')
        with image:
            data = BytesIO()
            image.save(data, 'JPEG', quality=85)
        return {'kind': 'image', 'url': 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode('ascii')}

    def jobs(self, _):
        rows = self.catalog.rows("""
            SELECT id,kind,state,message,name,batch IS NOT NULL AS has_batch FROM cloud_jobs WHERE 1
            """ + self.upload_filter + """
            ORDER BY CASE WHEN state IN ('running','cancelling') THEN 0 ELSE 1 END,updated DESC LIMIT 100
            """)
        for row in rows:
            row['cancellable'] = row['state'] in ('queued','running','paused') or (
                row['state'] in ('cancel_unknown','failed') and bool(row['has_batch']))
            row.pop('has_batch')
            row['retryable'] = row['state'] in ('paused','failed')
            if row['state'] == 'running' and row['message'].startswith('传输进度 '):
                row['progress'] = int(row['message'].split()[-1][:-1])
        counts = self.catalog.one("""
            SELECT COUNT(CASE WHEN state IN ('queued','running','paused') OR
                       (state IN ('cancel_unknown','failed') AND batch IS NOT NULL) THEN 1 END) cancellable,
                   COUNT(CASE WHEN state IN ('paused','failed') THEN 1 END) retryable
            FROM cloud_jobs WHERE 1
            """ + self.upload_filter)
        return {'items': rows, 'counts': counts}

    def pick_upload(self, _):
        picked = self.native.choose_upload_files()
        if not picked:
            return None
        if len(picked) > 100:
            raise ValueError('每次最多选择 100 个文件')
        try:
            paths = list(dict.fromkeys(selected_path(path) for path in picked))
            if any(not path.is_file() for path in paths):
                raise ValueError('请选择本地普通文件')
            entries = [archive_file_identity(path) for path in paths]
        except OSError:
            raise ValueError('选中的文件已移动或不可读取，请重新选择') from None
        identifier = uuid4().hex
        with self.catalog.lock:
            self.upload_picks[identifier] = entries
            if len(self.upload_picks) > 8:
                self.upload_picks.popitem(last=False)
        return {'id':identifier,'names':[path.name for path in paths],'bytes':sum(entry[3] for entry in entries)}

    def start_upload(self, p):
        with self.catalog.lock:
            entries = self.upload_picks.get(p['id'])
        if entries is None:
            raise ValueError('请重新选择需要上传的文件')
        for entry in entries:
            self.upload_source(entry)
        account = self.require_account()
        with self.catalog.lock:
            if self.upload_picks.get(p['id']) is not entries:
                raise ValueError('这批文件已提交，请在上传任务中查看')
            active = {
                tuple(json.loads(row['picked']))
                for row in self.catalog.rows("""
                    SELECT json_extract(payload,'$.selected_file') picked FROM cloud_jobs
                    WHERE kind='upload' AND json_extract(payload,'$.account')=?
                      AND json_type(payload,'$.selected_file')='array'
                      AND (state IN ('queued','running','paused','cancelling','cancel_unknown')
                       OR (state='failed' AND operation IS NOT NULL))
                    """, (account,))
            }
            jobs = [(uuid4().hex,Path(entry[0]).name,json.dumps(dict(account=account,selected_file=entry)),time.time())
                    for entry in entries if tuple(entry) not in active]
            if not jobs:
                raise ValueError('文件已有待处理的上传任务')
            with self.catalog.db:
                self.catalog.db.executemany("""
                    INSERT INTO cloud_jobs(id,kind,state,name,payload,updated) VALUES(?,'upload','queued',?,?,?)
                    """, jobs)
            self.upload_picks.pop(p['id'],None)
            self.start_worker()
        return {'count':len(jobs)}

    @staticmethod
    def upload_source(entry):
        try:
            path = selected_path(entry[0])
            if not path.is_file() or archive_file_identity(path) != entry:
                raise ValueError('选中的文件发生变化，请重新选择')
            return path
        except OSError:
            raise ValueError('选中的文件已移动或不可读取，请重新选择') from None

    def backup(self, _):
        destination = self.native.choose_cloud_backup(save=True)
        if not destination:
            return False
        entries = []
        with self.catalog.lock:
            for remote in self.catalog.rows('SELECT * FROM cloud_files ORDER BY id'):
                variant = self.catalog.variant(remote['variant_id'])
                entries.append(dict(
                    remote={k: remote[k] for k in ('account', 'remote', 'parent', 'name', 'filename', 'bytes', 'sha256')},
                    media={k: variant[k] for k in ('platform', 'scope', 'native_id', 'author', 'text',
                           'media_native', 'media_type', 'quality', 'extension', 'width', 'height')},
                    tags=self.catalog.tags(variant),
                    libraries=[r['library'] for r in self.catalog.rows(
                        'SELECT library FROM cloud_links WHERE content_id=?', (variant['content_id'],))]))
        temp = destination.with_name(destination.name + '.' + uuid4().hex + '.tmp')
        try:
            with temp.open('x', encoding='utf-8') as handle:
                json.dump(dict(version=1, entries=entries), handle, ensure_ascii=False)
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
        return True

    def restore(self, _):
        source = self.native.choose_cloud_backup(save=False)
        if not source:
            return False
        if source.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('索引备份超过 64 MiB')
        with source.open(encoding='utf-8') as handle:
            backup = json.load(handle)
        if not isinstance(backup, dict) or backup.get('version') != 1 or not isinstance(backup.get('entries'), list):
            raise ValueError('光鸭索引备份格式无效')
        clean = [self.validate_backup(entry) for entry in backup['entries']]
        library_ids = {item['id'] for item in self.manager.items}
        with self.catalog.lock:
            for remote, media, tags, libraries in clean:
                key = (remote['account'], remote['remote'])
                if self.catalog.one('SELECT 1 FROM cloud_files WHERE account=? AND remote=?', key):
                    continue
                model = self.catalog.store.model
                content = model.content(media['platform'], media['scope'], media['native_id'], media['author'], media['text'])
                resource = model.media(content, media['media_native'], media['media_type'])
                variant = model.variant(resource, media['quality'], media['extension'], media['width'], media['height'])
                if self.catalog.one("SELECT 1 FROM catalog_messages WHERE platform='x' AND message_id=?",
                                    (media['native_id'],)) or media['platform'] != 'x':
                    previous = self.catalog.tags(self.catalog.variant(variant))
                    merged = list(dict.fromkeys([*previous, *tags]))[:20]
                    self.catalog.set_tags(variant, merged)
                else:
                    for tag in tags:
                        key_, name = normalize_tag(tag)
                        self.catalog.write('INSERT OR IGNORE INTO cloud_tags VALUES(?,?,?)', (content, key_, name))
                for library in library_ids.intersection(libraries):
                    self.catalog.write('INSERT OR IGNORE INTO cloud_links VALUES(?,?)', (content, library))
                self.catalog.upsert(variant, **remote)
                self.catalog.write("UPDATE cloud_files SET state='unconfirmed' WHERE account=? AND remote=?", key)
        self.runtime.emit({'type': 'changed'})
        return True

    @staticmethod
    def validate_backup(entry):
        try:
            remote, media, tags, libraries = (entry[k] for k in ('remote', 'media', 'tags', 'libraries'))
            strings = ('account', 'remote', 'parent', 'name', 'filename', 'sha256')
            if any(type(remote[k]) is not str or len(remote[k]) > 1024 for k in strings):
                raise ValueError()
            if not remote['account'] or not remote['remote'] or type(remote['bytes']) is not int or not 0 <= remote['bytes'] < 2**63:
                raise ValueError()
            checksum = remote['sha256']
            if checksum and (len(checksum) != 64 or any(c not in '0123456789abcdef' for c in checksum)):
                raise ValueError()
            if file_type(remote['name']) != (media['media_type'], media['extension']):
                raise ValueError()
            if media['platform'] not in ('x', 'guangyapan'):
                raise ValueError()
            if not media['native_id'] or not media['media_native'] or (media['platform'] == 'guangyapan' and media['scope'] != remote['account']):
                raise ValueError()
            for k in ('scope', 'native_id', 'text', 'media_native', 'quality'):
                if type(media[k]) is not str or len(media[k]) > (100000 if k == 'text' else 500):
                    raise ValueError()
            if media['author'] is not None and (type(media['author']) is not str or len(media['author']) > 500):
                raise ValueError()
            if any(type(media[k]) is not int or not 0 <= media[k] <= 1000000 for k in ('width','height')):
                raise ValueError()
            if type(tags) is not list or len(tags) > 20 or type(libraries) is not list:
                raise ValueError()
            for tag in tags:
                normalize_tag(tag)
            if any(type(library) is not str or len(library) > 200 for library in libraries):
                raise ValueError()
            result = {k: remote[k] for k in (*strings[:-1], 'bytes')}
            result['size'] = result.pop('bytes')
            result['digest'] = checksum
            return result, media, tags, libraries
        except (AttributeError, TypeError, KeyError, ValueError):
            raise ValueError('光鸭索引备份包含无效记录') from None

    def enqueue(self, jobs):
        with self.catalog.lock, self.catalog.db:
            occupied = set()
            if any(kind == 'upload' for kind, _, _ in jobs):
                occupied = {(r['account'], r['variant_id']) for r in self.catalog.rows("""
                    SELECT json_extract(j.payload,'$.account') account,l.variant_id
                    FROM cloud_jobs j JOIN model_locations l ON l.id=json_extract(j.payload,'$.location')
                    WHERE j.kind='upload' AND (j.state IN ('queued','running','paused','cancelling','cancel_unknown')
                       OR (j.state='failed' AND j.operation IS NOT NULL))
                    """)}
            inserted = 0
            for kind, name, payload in jobs:
                if kind == 'upload':
                    location = self.catalog.one('SELECT variant_id FROM model_locations WHERE id=?', (payload['location'],))
                    if not location:
                        continue
                    identity = payload['account'], location['variant_id']
                    if identity in occupied:
                        continue
                    occupied.add(identity)
                self.catalog.db.execute('INSERT INTO cloud_jobs(id,kind,state,name,payload,updated) VALUES(?,?,?,?,?,?)',
                                        (uuid4().hex, kind, 'queued', name, json.dumps(payload), time.time()))
                inserted += 1
            if inserted:
                self.start_worker()
        return inserted

    def start_worker(self):
        # Call under the catalog lock so updates and the worker's queue scan are ordered.
        if self.worker is None or not self.worker.is_alive():
            self.stop.clear()
            self.worker = threading.Thread(target=self.run_queue, daemon=True, name='x2lt-cloud')
            self.worker.start()

    def import_folder(self, p):
        account = self.require_account()
        return self.enqueue([('import', p.get('name') or '光鸭文件夹',
                              dict(account=account, parent=p['parent'], recursive=p.get('recursive', False)))])

    def upload(self, p):
        account = self.require_account()
        roots = self.roots()
        jobs = []
        seen = set()
        for job_id in dict.fromkeys(p['ids']):
            row = self.catalog.one("SELECT * FROM model_locations WHERE legacy_job_id=? AND state='completed'", (job_id,))
            if not row:
                continue
            try:
                path = self.catalog.local_path(row, roots)
            except (OSError, ValueError):
                continue
            if row['variant_id'] in seen:
                continue
            seen.add(row['variant_id'])
            library = next((x for x in self.manager.items if x['path'] == row['output']), None)
            if library:
                jobs.append(('upload', path.name, dict(account=account, location=row['id'],
                             parent=p['parent'], library=library['id'])))
        if not jobs:
            raise ValueError('没有可上传的已保存媒体')
        if not self.enqueue(jobs):
            raise ValueError('这些媒体已有待处理上传，请在云盘任务中查看或重试')
        return True

    def archive_plan(self, p):
        if 'ids' in p and not p['ids']:
            raise ValueError('请选择要归档的内容')
        account = self.require_account()
        library = self.manager.get(p['library_id'])
        args = [account, library['path'], library['id']]
        scope = ''
        if ids := p.get('ids'):
            scope = " AND s.platform='x' AND c.native_id IN (" + ','.join('?' for _ in ids) + ')'
            args.extend(ids)
        self.catalog.store.catalog.ensure_ready()
        rows = self.catalog.rows("""
            SELECT l.*,v.media_id,v.width,v.height,c.id AS content,c.native_id,c.text,c.author,
                   s.platform,s.scope,s.label,
                   COALESCE(a.author_key,'') author_key,COALESCE(a.handle,'') handle,
                   EXISTS(SELECT 1 FROM cloud_files f WHERE f.variant_id=v.id AND f.account=?
                          AND f.state='present' AND f.sha256<>'' AND f.sha256=l.sha256) known
            FROM model_locations l JOIN model_variants v ON v.id=l.variant_id
            JOIN model_media m ON m.id=v.media_id JOIN model_contents c ON c.id=m.content_id
            JOIN model_sources s ON s.id=c.source_id
            LEFT JOIN catalog_messages cm ON s.platform='x' AND cm.platform=s.platform AND cm.message_id=c.native_id
            LEFT JOIN catalog_authors a ON a.platform=cm.platform AND a.author_key=cm.author_key
            WHERE l.state='completed' AND (l.output=? OR EXISTS(
                SELECT 1 FROM cloud_links link WHERE link.content_id=c.id AND link.library=?))
            """ + scope + ' ORDER BY v.width*v.height DESC,l.bytes DESC,l.id ASC', args)
        roots = self.roots()
        records, unavailable = {}, set()
        physical = set()
        for row in rows:
            media = row['media_id']
            if media in records:
                continue
            try:
                path = self.catalog.local_path(row, roots)
                stat = path.stat()
            except (OSError, ValueError):
                unavailable.add(media)
                continue
            if (stat.st_dev, stat.st_ino) in physical and stat.st_ino:
                continue
            if stat.st_ino:
                physical.add((stat.st_dev, stat.st_ino))
            platform = row['platform']
            if platform in ('x', 'twitter'):
                group = row['author_key'] or row['author'] or ''
                label = row['handle'] or row['author'] or ''
            elif platform == 'telegram':
                group = row['scope'] or row['author'] or ''
                label = row['author'] or row['scope']
            else:
                group = '' if platform == 'local' else row['author'] or ''
                label = group
            records[media] = dict(platform=platform, group=group, group_label=label,
                source_label=row['label'], content=row['content'], location=row['id'], filename=path.name,
                title=next(iter(row['text'].splitlines()), path.name)[:180], bytes=stat.st_size,
                fingerprint=[str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns],
                tags=self.catalog.tags(dict(row, content_id=row['content'])),
                known=bool(row['known'] and row['bytes'] == stat.st_size))
        namespace = json.dumps([account, p['parent'], library['id']], separators=(',', ':'))
        reserved = {}
        for row in self.catalog.rows('SELECT parent_key,folded_name,owner FROM cloud_archive_names WHERE scope=?',
                                     (namespace,)):
            reserved.setdefault(row['parent_key'], {})[row['folded_name']] = row['owner']
        decisions, folders, aliases, choices, claims = organize(
            records.values(), p['mode'], p.get('threshold', 0), p.get('aliases', {}), p.get('tag_overrides', {}), reserved)
        prefix = ['X2LT归档', 'lib-' + hashlib.sha256(library['id'].encode()).hexdigest()[:12]]
        identifier = uuid4().hex
        jobs = [('upload', row['filename'], dict(account=account, location=row['location'],
                 parent=p['parent'], library=library['id'], archive_parts=prefix + parts,
                 archive_file=row['fingerprint'])) for row, parts in decisions]
        with self.archive_lock:
            self.archive_plans[identifier] = account, jobs, namespace, claims
            if len(self.archive_plans) > 8:
                self.archive_plans.popitem(last=False)
        known = sum(row['known'] for row in records.values())
        return dict(id=identifier, media_count=len(records), queued_count=len(records)-known,
                    known_count=known, bytes=sum(row['bytes'] for row in records.values() if not row['known']),
                    unavailable_count=len(unavailable - records.keys()),
                    folders=[dict(folder, path=prefix + folder['path']) for folder in folders],
                    aliases=aliases, tag_choices=choices)

    def archive_start(self, p):
        with self.archive_lock:
            plan = self.archive_plans.get(p['id'])
        if not plan:
            raise ValueError('归档预览已失效，请重新预览')
        account, jobs, namespace, claims = plan
        self.require_account(account)
        if not jobs:
            raise ValueError('没有可归档的本地媒体')
        with self.catalog.lock, self.catalog.db:
            for (parent, folded), owner in claims.items():
                existing = self.catalog.db.execute("""
                    SELECT owner FROM cloud_archive_names WHERE scope=? AND parent_key=? AND folded_name=?
                    """, (namespace, parent, folded)).fetchone()
                if existing and existing['owner'] != owner:
                    raise ValueError('已有其他归档占用同名目录，请重新预览')
                self.catalog.db.execute('INSERT OR IGNORE INTO cloud_archive_names VALUES(?,?,?,?)',
                                        (namespace, parent, folded, owner))
            queued = self.enqueue(jobs)
        with self.archive_lock:
            self.archive_plans.pop(p['id'], None)
        return {'queued': queued}

    def download(self, p):
        remote = self.catalog.remote(p['id'])
        self.require_account(remote['account'])
        library = self.manager.get(p['library_id'])
        return self.enqueue([('download', remote['filename'], dict(account=remote['account'],
                              remote=p['id'], library=library['id']))])

    def release(self, p):
        if self.runtime.busy() or self.runtime.removing():
            raise ValueError('请先等待本地下载、移除或媒体整理完成')
        local = self.catalog.local(p['id'])
        path = self.catalog.local_path(local, self.roots())
        account = self.require_account()
        remote = self.catalog.one("""
            SELECT * FROM cloud_files WHERE variant_id=? AND account=? AND state='present' AND sha256<>''
            ORDER BY id DESC LIMIT 1
            """, (local['variant_id'], account))
        if not remote:
            raise ValueError('没有可校验的光鸭副本')
        return self.enqueue([('release', path.name, dict(account=account, location=p['id'], remote=-remote['id']))])

    def job_scope(self, p):
        if p.get('all') is True and 'id' not in p:
            return self.upload_filter, ()
        if 'id' in p and p.get('all') is not True:
            return self.upload_filter + ' AND id=?', (p['id'],)
        raise ValueError('请选择一个任务或全部待处理任务')

    def retry(self, p):
        scope, args = self.job_scope(p)
        with self.catalog.lock, self.catalog.db:
            changed = self.catalog.db.execute("""
                UPDATE cloud_jobs SET state='queued',message='',updated=?
                WHERE state IN ('paused','failed')
                """ + scope, (time.time(), *args)).rowcount
            if not changed:
                raise ValueError('没有可重试的任务')
            self.start_worker()
        return {'count': changed}

    def cancel_jobs(self, p):
        scope, args = self.job_scope(p)
        with self.catalog.lock, self.catalog.db:
            changed = self.catalog.db.execute("""
                UPDATE cloud_jobs SET state=CASE WHEN state='running' OR batch IS NOT NULL OR operation IS NOT NULL
                    THEN 'cancelling' ELSE 'cancelled' END,
                    message=CASE WHEN state='running' OR batch IS NOT NULL OR operation IS NOT NULL
                    THEN '正在确认取消状态' ELSE '已取消，未发送媒体文件' END,updated=?
                WHERE (state IN ('queued','running','paused') OR
                       (state IN ('cancel_unknown','failed') AND batch IS NOT NULL))
                """ + scope, (time.time(), *args)).rowcount
            if not changed:
                raise ValueError('没有可取消的任务')
            if self.catalog.one("SELECT 1 FROM cloud_jobs WHERE state='cancelling'" + self.upload_filter):
                self.start_worker()
        self.runtime.emit({'type': 'changed'})
        return {'count': changed}

    def check_cancel(self, job_id):
        if self.catalog.one("SELECT 1 FROM cloud_jobs WHERE id=? AND state='cancelling'", (job_id,)):
            raise TransferCancelled()

    def settle_cancel(self, job, payload):
        saved = self.catalog.one('SELECT batch,operation FROM cloud_jobs WHERE id=?', (job['id'],))
        batch = saved['batch']
        if not batch:
            if saved['operation']:
                return 'cancel_unknown', '已停止本地任务，未取得批次编号。请在光鸭核对目标目录'
            return 'cancelled', '已取消，未发送媒体文件'
        try:
            self.require_account(payload['account'])
            result = self.provider.stop(batch)
            until = time.monotonic() + 20
            while result.get('code') == 'WORKER_STOP_PENDING' and time.monotonic() < until:
                if self.stop.wait(1):
                    break
                result = self.provider.batch(batch)
        except (OSError, ValueError):
            return 'cancel_unknown', '未能确认云端取消，可再次核对取消状态'
        data = result.get('data') or {}
        state = result.get('batch_state') or data.get('batch_state')
        if state == 'STOPPED' and not data.get('remote_execution_may_continue'):
            return 'cancelled', '已停止，已完成的云端文件仍保留'
        if state == 'SUCCEEDED':
            return 'paused', '取消前云端已完成，点重试可登记原结果'
        return 'cancel_unknown', '本地已停止，云端结果待核对，未再次上传'

    def run_queue(self):
        while not self.stop.is_set():
            with self.catalog.lock:
                job = self.catalog.one("""
                    SELECT * FROM cloud_jobs WHERE state IN ('queued','cancelling')
                    """ + self.upload_filter + """
                    ORDER BY CASE WHEN state='cancelling' THEN 0 ELSE 1 END,rowid LIMIT 1
                    """)
                if not job:
                    self.worker = None
                    return
                if job['state'] == 'queued':
                    self.catalog.write("UPDATE cloud_jobs SET state='running',updated=? WHERE id=?", (time.time(), job['id']))
            message = ''
            try:
                payload = json.loads(job['payload'])
                self.check_cancel(job['id'])
                self.require_account(payload['account'])
                message = getattr(self, 'do_' + job['kind'])(job, payload) or ''
                state = 'completed'
            except TransferCancelled:
                state, message = self.settle_cancel(job, payload)
                if state == 'cancelled' and 'selected_file' in payload:
                    self.clear_selected_stage(job,payload)
            except TransferPaused as exc:
                state, message = 'paused', str(exc)
            except ValueError as exc:
                state, message = 'failed', str(exc)
            except Exception:
                state, message = 'failed', '光鸭任务未完成，请检查本地文件和磁盘空间'
            with self.catalog.lock:
                # A stop may arrive while a network request is returning with an error.
                cancelling = self.catalog.one("SELECT 1 FROM cloud_jobs WHERE id=? AND state='cancelling'", (job['id'],))
                if not cancelling or state in ('cancelled','cancel_unknown','completed') or message.startswith('取消前'):
                    self.catalog.write('UPDATE cloud_jobs SET state=?,message=?,updated=? WHERE id=?',
                                       (state, message, time.time(), job['id']))
            self.runtime.emit({'type': 'changed'})

    def close(self):
        self.stop.set()
        worker = self.worker
        if worker:
            worker.join(timeout=0.1)
            if worker.is_alive():
                return False
        if self.directories is not None:
            self.directories.close()
        if self.playback is not None:
            self.playback.close()
        return True

    def scan(self, parent):
        for page in range(10000):
            if self.stop.is_set():
                raise TransferPaused('任务已暂停，可继续')
            result = self.provider.list(parent, page)
            yield from result['items']
            if not result.get('has_more'):
                return
        raise ValueError('目录分页超过上限')

    def do_import(self, job, p):
        pending, visited = [p['parent']], set()
        while pending:
            self.check_cancel(job['id'])
            parent = pending.pop()
            if parent in visited:
                continue
            if len(visited) >= 10000:
                raise ValueError('子目录数量超过上限')
            visited.add(parent)
            seen = set()
            for item in self.scan(parent):
                self.check_cancel(job['id'])
                if item['type'] == 'directory' and p['recursive']:
                    pending.append(item['id'])
                elif self.catalog.index(p['account'], parent, item):
                    seen.add(item['id'])
            for remote in self.catalog.rows('SELECT id,remote FROM cloud_files WHERE account=? AND parent=?', (p['account'], parent)):
                if remote['remote'] not in seen:
                    self.catalog.write("UPDATE cloud_files SET state='unconfirmed' WHERE id=?", (remote['id'],))

    def folder(self, account, parent, name):
        known = self.catalog.one('SELECT remote FROM cloud_folders WHERE account=? AND parent=? AND name=?',
                                 (account, parent, name))
        if known:
            return known['remote']
        args = account, parent, name
        receipt = self.catalog.one(
            'SELECT request_id,task_id FROM cloud_folder_requests WHERE account=? AND parent=? AND name=?', args)
        if not receipt:
            remote = self.existing_folder(parent, name)
            if remote:
                self.catalog.write('INSERT OR IGNORE INTO cloud_folders VALUES(?,?,?,?)', (*args, remote))
                return remote
            receipt = dict(request_id=str(uuid4()), task_id=str(uuid4()))
            self.catalog.write('INSERT INTO cloud_folder_requests VALUES(?,?,?,?,?)',
                               (*args, receipt['request_id'], receipt['task_id']))
        try:
            remote = self.provider.mkdir(parent, name, **receipt)
        except GuangyapanError as exc:
            if exc.code != 'REMOTE_NAME_CONFLICT':
                raise
            remote = self.existing_folder(parent, name)
            if not remote:
                raise
        self.catalog.write('INSERT OR IGNORE INTO cloud_folders VALUES(?,?,?,?)', (*args, remote))
        return remote

    def existing_folder(self, parent, name):
        matches = [item for item in self.scan(parent) if item['name'] == name]
        if len(matches) > 1 or (matches and matches[0]['type'] != 'directory'):
            raise ValueError('光鸭目标存在同名文件或多个目录，请选择其他位置')
        return matches[0]['id'] if matches else None

    def operation(self, job, p, op):
        self.require_account(p['account'])
        with self.catalog.lock:
            self.check_cancel(job['id'])
            old = self.catalog.one('SELECT batch,operation FROM cloud_jobs WHERE id=?', (job['id'],))
            batch = old['batch']
            if old['operation']:
                if json.loads(old['operation']) != op:
                    raise ValueError('此任务的原始传输目标已改变')
            else:
                self.catalog.write('UPDATE cloud_jobs SET operation=? WHERE id=?', (json.dumps(op), job['id']))
        request_id = task = str(UUID(job['id']))
        try:
            response = self.provider.batch(batch) if batch else self.provider.submit(op, request_id, task)
        except ValueError as exc:
            raise TransferPaused(str(exc)) from None
        resumed = False
        while True:
            data = response.get('data') or {}
            duplicate = response.get('code') == 'DUPLICATE_SUSPECTED'
            new_batch = response.get('batch_id') or data.get('batch_id') or (data.get('existing_batch_id') if duplicate else None)
            if new_batch and new_batch != batch:
                batch = new_batch
                self.catalog.write('UPDATE cloud_jobs SET batch=? WHERE id=?', (batch, job['id']))
            states = {data.get('batch_state'), response.get('batch_state')}
            for item in data.get('items', []):
                if item.get('operation_id') == op['id'] and item.get('state') == 'SUCCEEDED' and (batch is None or 'SUCCEEDED' in states):
                    return item.get('result') or {}
            code = response.get('code')
            self.check_cancel(job['id'])
            if duplicate and new_batch:
                try:
                    response = self.provider.batch(batch)
                except ValueError as exc:
                    raise TransferPaused(str(exc)) from None
                continue
            if states & {'FAILED', 'PARTIAL', 'STOPPED', 'SUCCEEDED'} or code in {'UPLOAD_RESULT_UNKNOWN', 'REMOTE_NAME_CONFLICT'}:
                raise ValueError(f'光鸭传输未完成（{code or "FAILED"}），请核对目标后重新操作')
            if not batch:
                if response.get('retryable'):
                    raise TransferPaused(f'光鸭任务等待继续（{code or "PENDING"}）')
                raise ValueError(f'光鸭传输未受理（{code or "UNKNOWN"}）')
            if self.stop.wait(1):
                raise TransferPaused('窗口关闭后可继续查询原任务')
            self.check_cancel(job['id'])
            if code not in (None, 'OK', 'SUBMIT_PENDING') and 'RUNNING' not in states:
                raise TransferPaused(f'暂时无法核对原光鸭任务（{code}）')
            if response.get('retryable') and code not in (None, 'OK', 'SUBMIT_PENDING') and not resumed:
                resumed = True
                try:
                    response = self.provider.submit(op, request_id, task)
                except ValueError as exc:
                    raise TransferPaused(str(exc)) from None
            elif response.get('retryable') and code not in (None, 'OK', 'SUBMIT_PENDING'):
                raise TransferPaused(f'光鸭任务等待继续（{code}）')
            else:
                if code not in (None, 'OK', 'SUBMIT_PENDING'):
                    raise ValueError(f'光鸭传输未完成（{code}）')
                progress = (data.get('progress') or {}).get('percent')
                caption = f'传输进度 {progress:.0f}%' if type(progress) in (float, int) and 0 <= progress <= 100 else '正在等待光鸭传输完成'
                self.catalog.write('UPDATE cloud_jobs SET message=? WHERE id=?', (caption, job['id']))
                try:
                    response = self.provider.batch(batch)
                except ValueError as exc:
                    raise TransferPaused(str(exc)) from None

    def stage(self, directory, job):
        directory.mkdir(parents=True, exist_ok=True)
        stage = directory / job['id']
        stage.mkdir(exist_ok=True)
        if stage.is_symlink() or stage.resolve() != stage:
            raise ValueError('传输临时路径不安全')
        return stage

    def selected_chunk_guard(self, job):
        self.check_cancel(job['id'])
        if self.stop.is_set():
            raise TransferPaused('任务已暂停，可继续')

    def selected_checksum(self, job, path):
        checksum = hashlib.sha256()
        with path.open('rb') as handle:
            while chunk := handle.read(8 * 1024 * 1024):
                self.selected_chunk_guard(job)
                checksum.update(chunk)
        return checksum.hexdigest()

    def stage_selected(self, job, p, target):
        entry = p['selected_file']
        if target.exists() or is_alias(target):
            if is_alias(target) or not target.is_file():
                raise ValueError('上传暂存文件无效')
            if p.get('sha256'):
                if target.stat().st_size != entry[3] or self.selected_checksum(job,target) != p['sha256']:
                    raise ValueError('上传暂存文件已改变，未再次提交')
                return
            target.unlink()
        if job['operation']:
            raise ValueError('原上传暂存文件已丢失，未再次提交')
        path = self.upload_source(entry)
        if shutil.disk_usage(target.parent).free < entry[3]:
            raise TransferPaused('暂存空间不足，释放磁盘空间后继续')
        temp = target.parent / (uuid4().hex + '.part')
        checksum = hashlib.sha256()
        def source_unchanged(handle):
            info = os.fstat(handle.fileno())
            return stat.S_ISREG(info.st_mode) and [info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns] == entry[1:]
        try:
            with path.open('rb') as source, temp.open('xb') as handle:
                if not source_unchanged(source):
                    raise ValueError('选中的文件发生变化，请重新选择')
                while chunk := source.read(8 * 1024 * 1024):
                    self.selected_chunk_guard(job)
                    checksum.update(chunk)
                    handle.write(chunk)
                if not source_unchanged(source):
                    raise ValueError('暂存期间选中的文件发生变化，请重新选择')
            self.upload_source(entry)
            if temp.stat().st_size != entry[3] or self.selected_checksum(job,temp) != checksum.hexdigest():
                raise ValueError('暂存文件校验失败')
            temp.rename(target)
            p['sha256'] = checksum.hexdigest()
            self.catalog.write('UPDATE cloud_jobs SET payload=? WHERE id=?', (json.dumps(p),job['id']))
        finally:
            temp.unlink(missing_ok=True)

    def existing_selected_upload(self, p, target):
        account, checksum, size = p['account'], p['sha256'], p['selected_file'][3]
        files = self.catalog.rows("""
            SELECT parent,remote,name FROM cloud_upload_receipts WHERE account=? AND sha256=? AND bytes=?
            UNION SELECT parent,remote,name FROM cloud_files
            WHERE account=? AND sha256=? AND bytes=? AND state='present'
            """, (account,checksum,size,account,checksum,size))
        directories = {}
        for file in files:
            if file['name'] == target.name:
                continue
            if file['parent'] not in directories:
                try:
                    directories[file['parent']] = {item['id']:item for item in self.scan(file['parent'])}
                except (OSError, ValueError):
                    raise TransferPaused('暂时无法核对已有云端副本，可稍后继续') from None
            match = directories[file['parent']].get(file['remote'])
            if match and match['type'] == 'file' and match['name'] == file['name'] and int(match['size']) == size:
                return True
        return False

    def selected_target(self, job, p):
        suffix = Path(p['selected_file'][0]).suffix.lower()
        extension = suffix if suffix[1:].isascii() and suffix[1:].isalnum() and len(suffix) <= 21 else ''
        return self.work / job['id'] / (job['id'] + extension)

    def clear_selected_stage(self, job, p):
        target = self.selected_target(job,p)
        try:
            if selected_path(target) == target and target.is_file():
                target.unlink()
        except (OSError, ValueError):
            pass

    def do_selected_upload(self, job, p):
        target = self.selected_target(job,p)
        receipt = self.catalog.one('SELECT * FROM cloud_upload_receipts WHERE job=? AND account=?',
                                   (job['id'],p['account']))
        if receipt:
            match = next((item for item in self.scan(receipt['parent']) if item['id'] == receipt['remote']),None)
            if not match or match['name'] != receipt['name'] or match['type'] != 'file' or int(match['size']) != receipt['bytes']:
                raise TransferPaused('原上传记录无法核对，请检查光鸭目录')
            self.clear_selected_stage(job,p)
            return '已核对原上传结果'
        if p.get('sha256') and not job['operation'] and self.existing_selected_upload(p,target):
            self.clear_selected_stage(job,p)
            return '已有云端副本，已跳过重复上传'
        self.stage(self.work,job)
        self.stage_selected(job,p,target)
        checksum, size = p['sha256'], p['selected_file'][3]
        older_only = " AND j.rowid<(SELECT rowid FROM cloud_jobs WHERE id=?)" if job['operation'] else ''
        unresolved = self.catalog.one("""
            SELECT 1 FROM cloud_jobs j
            LEFT JOIN model_locations l ON l.id=json_extract(j.payload,'$.location')
            WHERE j.id<>? AND j.kind='upload' AND json_extract(j.payload,'$.account')=?
              AND COALESCE(json_extract(j.payload,'$.sha256'),l.sha256)=?
              AND j.operation IS NOT NULL AND j.state IN ('running','paused','failed','cancelling','cancel_unknown','queued')
            """ + older_only, (job['id'],p['account'],checksum,*((job['id'],) if older_only else ())))
        if unresolved:
            if not job['operation']:
                self.clear_selected_stage(job,p)
            raise TransferPaused('此文件已有上传结果待核对，请先处理原任务')
        if self.existing_selected_upload(p,target):
            if job['operation']:
                raise TransferPaused('此文件已有其他云端副本，可取消旧任务')
            target.unlink()
            return '已有云端副本，已跳过重复上传'
        self.selected_chunk_guard(job)
        parent = (json.loads(job['operation'])['remote_parent_id'] if job['operation']
                  else self.folder(p['account'],'','X2LT上传'))
        result = self.operation(job,p,dict(id='transfer',type='upload',local_path=str(target),remote_parent_id=parent))
        remote = (result.get('file') or {}).get('fileId')
        if not isinstance(remote,str) or not remote:
            raise TransferPaused('光鸭未返回文件编号，无法核对上传')
        match = next((item for item in self.scan(parent) if item['id'] == remote),None)
        if not match or match['name'] != target.name or match['type'] != 'file' or int(match['size']) != size:
            raise TransferPaused('光鸭文件已提交，目录核对尚未通过')
        self.catalog.write("""
            INSERT OR REPLACE INTO cloud_upload_receipts(job,account,remote,parent,name,bytes,sha256)
            VALUES(?,?,?,?,?,?,?)
            """, (job['id'],p['account'],remote,parent,target.name,size,checksum))
        self.clear_selected_stage(job,p)

    def do_upload(self, job, p):
        if 'selected_file' in p:
            return self.do_selected_upload(job,p)
        row = self.catalog.local(p['location'])
        path = self.catalog.local_path(row, self.roots())
        variant = self.catalog.variant(row['variant_id'])
        if 'archive_file' in p:
            if archive_file_identity(path) != p['archive_file']:
                raise ValueError('预览后本地文件发生变化，请重新生成归档计划')
        older_only = ' AND j.rowid<(SELECT rowid FROM cloud_jobs WHERE id=?)' if job['operation'] else ''
        unresolved = self.catalog.one("""
                SELECT 1 FROM cloud_jobs j JOIN model_locations l ON l.id=json_extract(j.payload,'$.location')
                WHERE j.id<>? AND j.kind='upload' AND l.variant_id=? AND json_extract(j.payload,'$.account')=?
                  AND j.operation IS NOT NULL AND j.state IN ('running','paused','failed','cancelling','cancel_unknown','queued')
                """ + older_only, (job['id'], row['variant_id'], p['account'], *((job['id'],) if older_only else ())))
        if unresolved:
            raise TransferPaused('此媒体已有上传结果待核对，请先处理原任务')
        if job['operation']:
            if self.archive_already_saved(p['account'], row['variant_id'], path,
                                          exclude_name=job['id'] + '.' + variant['extension']):
                raise TransferPaused('此媒体已有其他云端副本，旧批次未再次提交，可取消该任务')
        else:
            if self.archive_already_saved(p['account'], row['variant_id'], path):
                return '已有云端副本，已跳过重复上传'
        self.check_cancel(job['id'])
        stage = self.stage(self.work, job)
        suffix = variant['extension']
        if not suffix.isalnum():
            raise ValueError('媒体扩展名无效')
        target = stage / (job['id'] + '.' + suffix)
        if not target.exists():
            if shutil.disk_usage(stage).free < path.stat().st_size:
                raise TransferPaused('暂存空间不足，释放磁盘空间后继续')
            temp = stage / (uuid4().hex + '.part')
            try:
                with path.open('rb') as source, temp.open('xb') as handle:
                    while chunk := source.read(8 * 1024 * 1024):
                        self.check_cancel(job['id'])
                        handle.write(chunk)
                temp.rename(target)
            finally:
                temp.unlink(missing_ok=True)
            if 'archive_file' in p and archive_file_identity(path) != p['archive_file']:
                target.unlink()
                raise ValueError('暂存期间本地文件发生变化，请重新生成归档计划')
        if target.is_symlink() or not target.is_file():
            raise ValueError('上传暂存文件无效')
        checksum = digest(target)
        size = target.stat().st_size
        old_op = json.loads(job['operation']) if job['operation'] else None
        parent = p['parent']
        if old_op:
            parent = old_op['remote_parent_id']
        else:
            name_parts = p.get('archive_parts')
            if not name_parts:
                date = datetime.fromtimestamp(job['updated'])
                name_parts = ('X2LT归档', 'lib-' + hashlib.sha256(p['library'].encode()).hexdigest()[:12],
                              date.strftime('%Y'), date.strftime('%m'))
            for name in name_parts:
                self.check_cancel(job['id'])
                parent = self.folder(p['account'], parent, name)
            existing = self.catalog.one("""
                SELECT * FROM cloud_files WHERE account=? AND variant_id=? AND sha256=? AND parent=? AND state='present'
                """, (p['account'], row['variant_id'], checksum, parent))
            if existing:
                match = next((item for item in self.scan(parent) if item['id'] == existing['remote']), None)
                if match and match['name'] == existing['name'] and int(match['size']) == size:
                    target.unlink()
                    return '已有云端副本，已跳过重复上传'
                self.catalog.write("UPDATE cloud_files SET state='unconfirmed' WHERE id=?", (existing['id'],))
        result = self.operation(job, p, dict(id='transfer', type='upload', local_path=str(target), remote_parent_id=parent))
        remote = (result.get('file') or {}).get('fileId')
        if not isinstance(remote, str) or not remote:
            raise ValueError('光鸭未返回文件编号，无法登记副本')
        match = next((item for item in self.scan(parent) if item['id'] == remote), None)
        if not match or match['name'] != target.name or int(match['size']) != size:
            raise TransferPaused('光鸭文件已提交，目录核对尚未通过')
        self.catalog.upsert(row['variant_id'], p['account'], remote, parent, target.name, path.name, size, checksum)
        target.unlink()

    def archive_already_saved(self, account, variant, path, exclude_name=''):
        size = path.stat().st_size
        files = self.catalog.rows("""
            SELECT * FROM cloud_files WHERE account=? AND variant_id=? AND bytes=?
                AND state='present' AND sha256<>'' AND name<>?
            """, (account, variant, size, exclude_name))
        if not files:
            return False
        checksum = digest(path)
        for file in files:
            if file['sha256'] != checksum:
                continue
            try:
                match = next((item for item in self.scan(file['parent']) if item['id'] == file['remote']), None)
            except (OSError, ValueError):
                raise TransferPaused('暂时无法核对已有云端副本，可稍后继续') from None
            if match and match['type'] == 'file' and int(match['size']) == size:
                return True
            self.catalog.write("UPDATE cloud_files SET state='unconfirmed' WHERE id=?", (file['id'],))
        return False

    def fetch(self, job, p, remote, stage):
        self.operation(job, p, dict(id='transfer', type='download',
                                    remote_id=remote['remote'], local_dir=str(stage)))
        files = [f for f in stage.iterdir() if f.is_file() and f.name == remote['name']]
        if len(files) != 1 or files[0].is_symlink() or files[0].stat().st_size != remote['bytes']:
            raise ValueError('下载文件大小或名称核对失败')
        path = files[0]
        checksum = digest(path)
        if remote['sha256'] and remote['sha256'] != checksum:
            raise ValueError('下载文件与归档时记录的哈希不一致')
        return path, checksum

    def do_download(self, job, p):
        remote = self.catalog.remote(p['remote'])
        library = self.manager.get(p['library'])
        variant = self.catalog.variant(remote['variant_id'])
        root = Path(library['path'])
        name = safe_filename(remote['filename'], variant['extension'])
        if not job['operation'] and remote['sha256']:
            for candidate in self.catalog.rows("""
                SELECT * FROM model_locations WHERE variant_id=? AND output=? AND state='completed'
                """, (variant['id'], str(root))):
                try:
                    path = self.catalog.local_path(candidate, self.roots())
                    if path.stat().st_size != remote['bytes'] or digest(path) != remote['sha256']:
                        continue
                except (OSError, ValueError):
                    continue
                self.link(dict(id=variant['id'], library_id=library['id'], linked=True))
                return
        dest = root / '光鸭媒体' / job['id'] / name
        with self.manager.lease(dest) as lease:
            lease.reserve(remote['bytes']*2)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.parent.resolve() != dest.parent:
                raise ValueError('目标媒体库目录已改变')
            if dest.exists():
                saved = self.catalog.one('SELECT path,sha256 FROM cloud_download_receipts WHERE job=?', (job['id'],))
                if not saved or saved['path'] != str(dest) or dest.is_symlink():
                    raise ValueError('本地目标已存在，未覆盖')
                checksum = digest(dest)
                if checksum != saved['sha256']:
                    raise ValueError('本地目标已发生变化，未覆盖')
            else:
                stage = self.stage(dest.parent, job)
                local, checksum = self.fetch(job, p, remote, stage)
                self.catalog.write('INSERT OR REPLACE INTO cloud_download_receipts VALUES(?,?,?)',
                                   (job['id'], str(dest), checksum))
                if os.name == 'nt':
                    local.rename(dest)
                else:
                    os.link(local, dest)
                    local.unlink()
                if not remote['sha256']:
                    self.catalog.write("UPDATE cloud_files SET sha256=? WHERE id=? AND sha256=''",
                                       (checksum, remote['id']))
            already = self.catalog.one("SELECT id FROM model_locations WHERE local_path=? AND variant_id=? AND state='completed'",
                                       (str(dest), variant['id']))
            if not already:
                self.catalog.store.model.location(variant['id'], root, dest, bytes=remote['bytes'], sha256=checksum)
            self.link(dict(id=variant['id'], library_id=library['id'], linked=True))

    def do_release(self, job, p):
        local = self.catalog.local(p['location'])
        remote = self.catalog.remote(p['remote'])
        path = self.catalog.local_path(local, self.roots())
        if self.runtime.busy() or self.runtime.removing():
            raise TransferPaused('请先等待本地下载、移除或媒体整理完成')
        if remote['variant_id'] != local['variant_id'] or not remote['sha256'] or digest(path) != remote['sha256']:
            raise ValueError('本地文件与光鸭归档版本不一致，原文件仍在')
        stage = self.stage(self.work, job)
        if shutil.disk_usage(stage).free < remote['bytes']*2 and not job['operation']:
            raise TransferPaused('磁盘不足，无法先下载校验光鸭副本')
        downloaded, checksum = self.fetch(job, p, remote, stage)
        if self.catalog.local_path(self.catalog.local(p['location']), self.roots()) != path:
            raise ValueError('校验期间本地文件发生变化，原文件仍在')
        job_id = local['legacy_job_id']
        for other in self.catalog.rows("SELECT * FROM jobs WHERE state NOT IN ('removed','removing')"):
            if other['id'] == job_id:
                if other['relocation_source']:
                    raise ValueError('本地媒体转移尚未完成，原文件仍在')
                continue
            if other['local_path'] and Path(other['local_path']).resolve() == path:
                raise ValueError('另一个记录使用同一本地文件，原文件仍在')
            if not other['local_path'] and original_path(other).resolve() == path:
                raise ValueError('另一个记录使用同一本地文件，原文件仍在')
        if self.catalog.one("SELECT 1 FROM model_locations WHERE state='completed' AND id<>? AND local_path=?",
                            (local['id'], str(path))):
            raise ValueError('另一个记录使用同一本地文件，原文件仍在')
        def recycle_if_active(target):
            with self.catalog.lock:
                self.check_cancel(job['id'])
                recycle_file(target)
        recycle_verified(path, remote['bytes'], checksum, recycle_if_active)
        self.catalog.write('INSERT OR REPLACE INTO cloud_released VALUES(?,?,?)', (local['id'], str(path), checksum))
        downloaded.unlink()
