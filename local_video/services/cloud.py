# SPDX-License-Identifier: GPL-2.0-only
"""Desktop Guangyapan library, transfers and durable location tracking."""
from datetime import datetime
import base64
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import threading
import time
from uuid import UUID, uuid4, uuid5

from ..catalog import normalize_tag
from ..cloud_catalog import CloudCatalog, file_type, safe_filename
from ..cloud_directory_cache import CloudDirectoryCache
from ..images import read_image
from ..guangyapan import Guangyapan
from ..guangyapan_previews import GuangyapanPreviews
from ..archive import original_path
from ..removal import recycle_file, recycle_verified


class TransferPaused(ValueError):
    pass


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


class CloudService:
    def __init__(self, data, store, manager, native, runtime, provider=None):
        self.data, self.manager, self.native, self.runtime = Path(data), manager, native, runtime
        self.catalog = CloudCatalog(store)
        self.provider = provider or Guangyapan(self.data)
        self.directories = CloudDirectoryCache(self.provider.list)
        self.thumbnails = GuangyapanPreviews(self.data, runtime.cancel)
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
            key = (self.thumbnails.register(account, item['id'])
                   if item['type'] == 'file' and file_type(item['name']) else None)
            items.append({**item, 'thumbnail_key': key} if key else item)
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
        return dict(items=[self.catalog.entry(row['id'], roots, self.account, self.thumbnails.register) for row in rows],
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

    def jobs(self, _):
        rows = self.catalog.rows('SELECT id,kind,state,message,name FROM cloud_jobs ORDER BY updated DESC LIMIT 100')
        for row in rows:
            if row['state'] == 'running' and row['message'].startswith('传输进度 '):
                row['progress'] = int(row['message'].split()[-1][:-1])
        return {'items': rows}

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
            for kind, name, payload in jobs:
                self.catalog.db.execute('INSERT INTO cloud_jobs(id,kind,state,name,payload,updated) VALUES(?,?,?,?,?,?)',
                                        (uuid4().hex, kind, 'queued', name, json.dumps(payload), time.time()))
            if self.worker is None or not self.worker.is_alive():
                self.stop.clear()
                self.worker = threading.Thread(target=self.run_queue, daemon=True, name='x2lt-cloud')
                self.worker.start()
        return True

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
        return self.enqueue(jobs)

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

    def retry(self, p):
        with self.catalog.lock:
            job = self.catalog.one('SELECT state FROM cloud_jobs WHERE id=?', (p['id'],))
            if not job or job['state'] != 'paused':
                raise ValueError('此任务没有可续接的进度')
            self.catalog.write("UPDATE cloud_jobs SET state='queued',message='' WHERE id=?", (p['id'],))
            if self.worker is None or not self.worker.is_alive():
                self.stop.clear()
                self.worker = threading.Thread(target=self.run_queue, daemon=True, name='x2lt-cloud')
                self.worker.start()
        return True

    def run_queue(self):
        while not self.stop.is_set():
            with self.catalog.lock:
                job = self.catalog.one("SELECT * FROM cloud_jobs WHERE state='queued' ORDER BY rowid LIMIT 1")
                if not job:
                    self.worker = None
                    return
                self.catalog.write("UPDATE cloud_jobs SET state='running',updated=? WHERE id=?", (time.time(), job['id']))
            message = ''
            try:
                payload = json.loads(job['payload'])
                self.require_account(payload['account'])
                getattr(self, 'do_' + job['kind'])(job, payload)
                state = 'completed'
            except TransferPaused as exc:
                state, message = 'paused', str(exc)
            except ValueError as exc:
                state, message = 'failed', str(exc)
            except Exception:
                state, message = 'failed', '光鸭任务未完成，请检查本地文件和磁盘空间'
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
        self.directories.close()
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
            parent = pending.pop()
            if parent in visited:
                continue
            if len(visited) >= 10000:
                raise ValueError('子目录数量超过上限')
            visited.add(parent)
            seen = set()
            for item in self.scan(parent):
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
        namespace = UUID('49d36817-de37-4817-b5a4-e6021e7559ae')
        request = str(uuid5(namespace, json.dumps([account, parent, name], ensure_ascii=False)))
        remote = self.provider.mkdir(parent, name, request_id=request, task_id=request)
        self.catalog.write('INSERT INTO cloud_folders VALUES(?,?,?,?)', (account, parent, name, remote))
        return remote

    def operation(self, job, p, op):
        self.require_account(p['account'])
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

    def do_upload(self, job, p):
        row = self.catalog.local(p['location'])
        path = self.catalog.local_path(row, self.roots())
        variant = self.catalog.variant(row['variant_id'])
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
                    shutil.copyfileobj(source, handle, length=1024 * 1024)
                temp.rename(target)
            finally:
                temp.unlink(missing_ok=True)
        if target.is_symlink() or not target.is_file():
            raise ValueError('上传暂存文件无效')
        checksum = digest(target)
        size = target.stat().st_size
        old_op = json.loads(job['operation']) if job['operation'] else None
        parent = p['parent']
        if old_op:
            parent = old_op['remote_parent_id']
        else:
            date = datetime.fromtimestamp(job['updated'])
            name_parts = ('X2LT归档', 'lib-' + hashlib.sha256(p['library'].encode()).hexdigest()[:12],
                          date.strftime('%Y'), date.strftime('%m'))
            for name in name_parts:
                parent = self.folder(p['account'], parent, name)
            existing = self.catalog.one("""
                SELECT * FROM cloud_files WHERE account=? AND variant_id=? AND sha256=? AND parent=? AND state='present'
                """, (p['account'], row['variant_id'], checksum, parent))
            if existing:
                match = next((item for item in self.scan(parent) if item['id'] == existing['remote']), None)
                if match and match['name'] == existing['name'] and int(match['size']) == size:
                    target.unlink()
                    return
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
        recycle_verified(path, remote['bytes'], checksum, recycle_file)
        self.catalog.write('INSERT OR REPLACE INTO cloud_released VALUES(?,?,?)', (local['id'], str(path), checksum))
        downloaded.unlink()
