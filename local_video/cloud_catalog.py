# SPDX-License-Identifier: GPL-2.0-only
"""Cloud locations and collection membership for the shared media model."""
import re
import time
from pathlib import Path

from .catalog import normalize_tag
from .runner import destination_for


IMAGES = {'jpg', 'jpeg', 'png', 'webp', 'gif', 'bmp', 'avif'}
VIDEOS = {'mp4', 'm4v', 'mov', 'mkv', 'webm', 'avi', 'wmv', 'ts'}


def file_type(name):
    extension = Path(name).suffix[1:].lower()
    if extension in IMAGES:
        return 'image', extension
    if extension in VIDEOS:
        return 'video', extension
    return None


def safe_filename(name, extension):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip(' .')
    stem = Path(name).stem[:140].rstrip(' .')
    if not stem or re.fullmatch(r'(?i)(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³]|conin\$|conout\$)', stem.split('.')[0]):
        stem = 'media_' + stem
    return f'{stem}.{extension}'


class CloudCatalog:
    def __init__(self, store, upload_only=False):
        self.store = store
        self.db, self.lock = store.db, store.lock
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS cloud_files(
                    id INTEGER PRIMARY KEY, variant_id INTEGER NOT NULL,
                    account TEXT NOT NULL, remote TEXT NOT NULL, parent TEXT NOT NULL,
                    name TEXT NOT NULL, filename TEXT NOT NULL, bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'present',
                    UNIQUE(account,remote));
                CREATE INDEX IF NOT EXISTS cloud_variant ON cloud_files(variant_id);
                CREATE TABLE IF NOT EXISTS cloud_links(
                    content_id INTEGER NOT NULL, library TEXT NOT NULL,
                    PRIMARY KEY(content_id,library));
                CREATE TABLE IF NOT EXISTS cloud_tags(
                    content_id INTEGER NOT NULL, tag TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(content_id,tag));
                CREATE TABLE IF NOT EXISTS cloud_released(
                    location INTEGER PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_jobs(
                    id TEXT PRIMARY KEY, kind TEXT NOT NULL, state TEXT NOT NULL,
                    name TEXT NOT NULL, payload TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    batch TEXT, operation TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_folders(
                    account TEXT NOT NULL, parent TEXT NOT NULL, name TEXT NOT NULL,
                    remote TEXT NOT NULL, PRIMARY KEY(account,parent,name));
                CREATE TABLE IF NOT EXISTS cloud_folder_requests(
                    account TEXT NOT NULL, parent TEXT NOT NULL, name TEXT NOT NULL,
                    request_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    PRIMARY KEY(account,parent,name));
                CREATE TABLE IF NOT EXISTS cloud_download_receipts(
                    job TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_archive_names(
                    scope TEXT NOT NULL, parent_key TEXT NOT NULL, folded_name TEXT NOT NULL, owner TEXT NOT NULL,
                    PRIMARY KEY(scope,parent_key,folded_name));
                CREATE TABLE IF NOT EXISTS cloud_upload_receipts(
                    job TEXT PRIMARY KEY, account TEXT NOT NULL, remote TEXT NOT NULL,
                    parent TEXT NOT NULL, name TEXT NOT NULL, bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS cloud_upload_digest ON cloud_upload_receipts(account,sha256,bytes);
            """)
            with self.db:
                scope = " AND kind='upload'" if upload_only else ""
                self.db.execute("UPDATE cloud_jobs SET state='paused' WHERE state IN ('running','queued')" + scope)

    def upsert(self, variant, account, remote, parent, name, filename, size, digest=''):
        with self.lock, self.db:
            self.db.execute("""
                INSERT INTO cloud_files(variant_id,account,remote,parent,name,filename,bytes,sha256)
                VALUES(?,?,?,?,?,?,?,?)
                ON CONFLICT(account,remote) DO UPDATE SET parent=excluded.parent,
                    name=excluded.name,filename=excluded.filename,state='present',
                    sha256=CASE WHEN bytes=excluded.bytes THEN sha256 ELSE '' END,
                    bytes=excluded.bytes
                """, (variant, account, remote, parent, name, filename, size, digest))
            self.db.execute("""
                INSERT OR IGNORE INTO model_saved(media_id,added_at)
                SELECT media_id,? FROM model_variants WHERE id=?
                """, (time.time(), variant))

    def index(self, account, parent, item):
        kind = file_type(item['name'])
        if item['type'] != 'file' or not kind:
            return False
        size = int(item['size'])
        if size < 0 or size > 2**63-1:
            return False
        existing = self.one('SELECT * FROM cloud_files WHERE account=? AND remote=?', (account, item['id']))
        if existing:
            variant = existing['variant_id']
            filename = item['name'] if not existing['sha256'] and existing['filename'] == existing['name'] else existing['filename']
        else:
            content = self.store.model.content('guangyapan', account, item['id'])
            media = self.store.model.media(content, item['id'], kind[0])
            variant = self.store.model.variant(media, 'original', kind[1])
            filename = item['name']
        self.upsert(variant, account, item['id'], parent, item['name'], filename, size)
        return True

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def write(self, sql, args=()):
        with self.lock, self.db:
            self.db.execute(sql, args)

    def variant(self, variant):
        row = self.one("""
            SELECT v.*,m.media_type,m.native_id AS media_native,c.id AS content_id,
                c.native_id,c.text,c.author,s.platform,s.scope
            FROM model_variants v JOIN model_media m ON m.id=v.media_id
            JOIN model_contents c ON c.id=m.content_id JOIN model_sources s ON s.id=c.source_id
            WHERE v.id=?
            """, (variant,))
        if not row:
            raise ValueError('找不到媒体记录')
        return row

    def local_path(self, row, roots):
        output = Path(row['output'])
        if str(output) not in roots:
            raise ValueError('本地位置所属的媒体库不可用')
        if row['legacy_job_id'] is not None:
            jobs = self.store.get_many([row['legacy_job_id']])
            if not jobs or jobs[0]['state'] != 'completed':
                raise ValueError('本地任务尚未完成')
            raw = destination_for(jobs[0])
            # A previous media transfer can place the file in an approved external location.
            registered = bool(jobs[0].get('local_path'))
        else:
            raw, registered = Path(row['local_path']), False
        path = raw.resolve(strict=True)
        if raw.is_symlink() or path != raw or not path.is_file() or (not registered and output not in path.parents):
            raise ValueError('本地文件路径已改变')
        return path

    def local(self, location):
        row = self.one("SELECT * FROM model_locations WHERE id=? AND state='completed'", (location,))
        if not row:
            raise ValueError('找不到本地位置')
        return row

    def remote(self, location):
        row = self.one('SELECT * FROM cloud_files WHERE id=?', (-location,)) if location < 0 else None
        if not row:
            raise ValueError('找不到光鸭位置')
        return row

    def tags(self, variant):
        generic = [r['name'] for r in self.rows('SELECT name FROM cloud_tags WHERE content_id=? ORDER BY tag', (variant['content_id'],))]
        if variant['platform'] == 'x' and not variant['scope']:
            legacy = [r['name'] for r in self.rows("""
                SELECT t.name FROM catalog_message_tags mt JOIN catalog_tags t ON t.normalized=mt.tag
                WHERE mt.platform='x' AND mt.message_id=? ORDER BY mt.tag
                """, (variant['native_id'],))]
            if self.one("SELECT 1 FROM catalog_messages WHERE platform='x' AND message_id=?", (variant['native_id'],)):
                return legacy
        return generic

    def set_tags(self, variant, tags):
        row = self.variant(variant)
        if row['platform'] == 'x' and not row['scope'] and self.one(
                "SELECT 1 FROM catalog_messages WHERE platform='x' AND message_id=?", (row['native_id'],)):
            self.store.catalog.set_tags(row['native_id'], tags)
            return
        labels = dict(normalize_tag(tag) for tag in tags)
        with self.lock, self.db:
            self.db.execute('DELETE FROM cloud_tags WHERE content_id=?', (row['content_id'],))
            self.db.executemany('INSERT INTO cloud_tags VALUES(?,?,?)',
                                ((row['content_id'], tag, name) for tag, name in labels.items()))

    def entry(self, variant, roots, active_account=None, thumbnail=None, view=None):
        row = self.variant(variant)
        files = self.rows('SELECT * FROM cloud_files WHERE variant_id=? ORDER BY id', (variant,))
        locals_ = self.rows("SELECT * FROM model_locations WHERE variant_id=? AND state='completed' ORDER BY id", (variant,))
        locations = []
        thumbnail_key = None
        view_key = None
        filename = files[0]['filename'] if files else f"{row['native_id']}_{row['media_native']}.{row['extension']}"
        byte_count = files[0]['bytes'] if files else 0
        for local in locals_:
            try:
                path = self.local_path(local, roots)
            except (OSError, ValueError):
                continue
            filename = path.name
            byte_count = local['bytes'] or byte_count
            released = self.one('SELECT path,sha256 FROM cloud_released WHERE location=?', (local['id'],))
            if released and released['path'] == str(path):
                # A recycled file may later be restored through Windows.
                self.write('DELETE FROM cloud_released WHERE location=?', (local['id'],))
            locations.append(dict(id=local['id'], kind='local', state='present',
                                  label=roots[local['output']], verification=local['verification']))
        for remote in files:
            state = remote['state'] if active_account in (None, remote['account']) else 'unavailable'
            if thumbnail and state == 'present' and thumbnail_key is None:
                thumbnail_key = thumbnail(remote['account'], remote['remote'])
            if view and state == 'present' and view_key is None:
                view_key = view(remote['account'], remote['remote'], row['media_type'])
            locations.append(dict(id=-remote['id'], kind='cloud', state=state, label='光鸭',
                                  verification='本地哈希已记录' if remote['sha256'] else '文件信息已登记'))
        return dict(id=variant, media_id=row['media_id'], content_id=row['content_id'],
            thumbnail_key=thumbnail_key,
            view_key=view_key,
            has_source=row['platform'] == 'x' and row['native_id'].isascii() and row['native_id'].isdigit(),
            title=next(
            (line.strip() for line in row['text'].splitlines() if line.strip()), filename),
            text=row['text'], author=row['author'] or '', kind=row['media_type'], filename=filename,
            bytes=byte_count, tags=self.tags(row),
            libraries=[r['library'] for r in self.rows('SELECT library FROM cloud_links WHERE content_id=?', (row['content_id'],))],
            locations=locations)
