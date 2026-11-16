# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Persistent local queue, independent of the original Telegram archive."""
from dataclasses import asdict
from pathlib import Path
import os
import sqlite3
import tempfile
import threading
import time


class Store:
    def __init__(self, path, defer_catalog=False):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.ignore_versions = {}
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
        try:
            self._backup_before_catalog(path)
        except Exception:
            self.db.close()
            raise
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, tweet_id TEXT NOT NULL, media_id TEXT NOT NULL,
                author TEXT NOT NULL, url TEXT NOT NULL, source TEXT NOT NULL,
                width INTEGER, height INTEGER, output TEXT NOT NULL, quality TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', error TEXT DEFAULT '',
                bytes INTEGER DEFAULT 0, sha256 TEXT DEFAULT '', updated REAL,
                verification TEXT DEFAULT '',
                UNIQUE(tweet_id, media_id, output, quality));
        ''')
        if 'verification' not in {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute("ALTER TABLE jobs ADD COLUMN verification TEXT DEFAULT ''")
            self.db.commit()
        for column in ('local_path', 'relocation_source'):
            if column not in {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {column} TEXT DEFAULT ''")
                self.db.commit()
        if 'force_download' not in {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN force_download INTEGER DEFAULT 0')
            self.db.commit()

        columns = {row[1] for row in self.db.execute('PRAGMA table_info(jobs)')}
        for name, default in [('media_type','video'),('extension','mp4'),('tweet_text',''),('preview_url','')]:
            if name not in columns:
                self.db.execute(f"ALTER TABLE jobs ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'")
        self.db.execute('CREATE TABLE IF NOT EXISTS ignored_tweets (tweet_id TEXT PRIMARY KEY, created REAL NOT NULL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_tweet_id ON jobs(tweet_id,id)')
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_state_id ON jobs(state,id)')
        self.db.execute('CREATE INDEX IF NOT EXISTS jobs_output_state ON jobs(output,state)')
        self.db.commit()

        from .catalog import Catalog
        self.catalog = Catalog(self.db, self.lock, defer=defer_catalog)
        from .resources import Resources
        self.resources = Resources(self.db,self.lock)

    def _backup_before_catalog(self, path):
        """Snapshot a populated legacy DB before any schema additions.

        The application InstanceLock prevents concurrent startup. Publishing a
        completed temporary backup with an exclusive hard link also prevents an
        accidental second Store from replacing the one-time recovery snapshot.
        """
        if str(path) == ':memory:':
            return
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'jobs' not in tables or 'catalog_migrations' in tables:
            return
        if not self.db.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():
            return
        source = Path(path).resolve()
        backup = source.with_name(source.name + '.pre-catalog-v1.bak')
        if backup.exists():
            return
        fd, temporary = tempfile.mkstemp(prefix=backup.name + '.', suffix='.tmp', dir=source.parent)
        os.close(fd)
        try:
            target = sqlite3.connect(temporary)
            try:
                self.db.backup(target, pages=500)
            finally:
                target.close()
            try:
                os.link(temporary, backup)
            except FileExistsError:
                # Another guarded startup already published a complete backup.
                pass
        finally:
            Path(temporary).unlink(missing_ok=True)

    def get_many(self, ids):
        ids = list(set(int(i) for i in ids))
        rows = []
        with self.lock:
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                rows.extend(dict(r) for r in self.db.execute('SELECT * FROM jobs WHERE id IN (' + ','.join('?' for _ in batch) + ')', batch))
        return rows

    def runnable_ids(self, output=None, recheck=False, force_id=None):
        where, args = ("state='completed'" if recheck else "(state IN ('pending','paused','failed') OR force_download=1)"), []
        if force_id is not None:
            where, args = 'id=?', [force_id]
        elif output:
            where += ' AND output=?'
            args.append(str(Path(output).resolve()))
        if not recheck:
            where += ' AND NOT EXISTS (SELECT 1 FROM ignored_tweets i WHERE i.tweet_id=jobs.tweet_id)'
        with self.lock:
            return [r[0] for r in self.db.execute('SELECT id FROM jobs WHERE ' + where + ' ORDER BY id', args)]

    def unfinished_page(self, offset=0, limit=100):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM jobs WHERE state!='completed' ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset))]

    def summary(self, output):
        with self.lock:
            counts = dict(self.db.execute('SELECT state,COUNT(*) FROM jobs GROUP BY state'))
            completed_output = self.db.execute("SELECT COUNT(*) FROM jobs WHERE state='completed' AND output=?", (str(Path(output).resolve()),)).fetchone()[0]
            active = [dict(r) for r in self.db.execute("SELECT * FROM jobs WHERE state='downloading' ORDER BY id DESC")]
            recent = [dict(r) for r in self.db.execute("SELECT * FROM jobs WHERE state='completed' ORDER BY id DESC LIMIT 3")]
        return counts, completed_output, active, recent

    def completed_for_output(self, output):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM jobs WHERE output=? AND state='completed' ORDER BY id DESC", (str(Path(output).resolve()),))]

    def enqueue(self, video, output, quality):
        values = asdict(video)
        if video.media_type == "image":
            quality = "image"
        values.update(output=str(Path(output).resolve()), quality=quality, updated=time.time())
        with self.lock, self.db:
            if self.is_ignored(video.tweet_id):
                return None
            self.db.execute('''INSERT INTO jobs(tweet_id,media_id,author,url,source,width,height,output,quality,updated,media_type,extension,tweet_text,preview_url)
                VALUES(:tweet_id,:media_id,:author,:url,:source,:width,:height,:output,:quality,:updated,:media_type,:extension,:tweet_text,:preview_url)
                ON CONFLICT(tweet_id,media_id,output,quality) DO UPDATE SET url=excluded.url, updated=excluded.updated,
                state=jobs.state, tweet_text=CASE WHEN excluded.tweet_text!='' THEN excluded.tweet_text ELSE jobs.tweet_text END, preview_url=CASE WHEN excluded.preview_url!='' THEN excluded.preview_url ELSE jobs.preview_url END''', values)
            self.catalog.upsert(video.tweet_id, video.author, video.author_id, video.display_name, video.avatar_url)
            return self.db.execute('SELECT id FROM jobs WHERE tweet_id=? AND media_id=? AND output=? AND quality=?', (video.tweet_id, video.media_id, values['output'], quality)).fetchone()[0]

    def list(self):
        with self.lock:
            return [dict(row) for row in self.db.execute('SELECT * FROM jobs ORDER BY id DESC')]

    def recover(self):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET state='paused' WHERE state='downloading'")

    def set_state(self, job_id, state, error='', **result):
        if state not in {'pending', 'downloading', 'paused', 'failed', 'completed'}:
            raise ValueError('invalid queue state')
        with self.lock, self.db:
            self.db.execute('UPDATE jobs SET state=?,error=?,bytes=?,sha256=?,verification=?,updated=? WHERE id=?', (state, error, result.get('bytes', 0), result.get('sha256', ''), result.get('verification', ''), time.time(), job_id))
            if state == 'completed':self.resources.record(job_id)

    def update_url(self, job_id, url):
        with self.lock, self.db:
            self.db.execute('UPDATE jobs SET url=? WHERE id=?', (url, job_id))

    def set_force(self, job_id, enabled):
        with self.lock, self.db:
            self.db.execute('UPDATE jobs SET force_download=? WHERE id=?', (int(enabled), job_id))

    def relocate(self, job_id, target, source):
        with self.lock, self.db:
            cursor = self.db.execute("UPDATE jobs SET local_path=?,relocation_source=?,updated=? WHERE id=? AND state='completed'", (str(Path(target).resolve()), str(Path(source).resolve()), time.time(), job_id))
            if cursor.rowcount != 1:
                raise RuntimeError('任务状态发生变化，已保留原视频')

    def finish_relocation(self, job_id):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET relocation_source='' WHERE id=?", (job_id,))

    def close(self):
        with self.lock:
            self.db.close()

    def update_metadata(self, job_id, media):
        with self.lock, self.db:
            self.db.execute("UPDATE jobs SET tweet_text=CASE WHEN ?!='' THEN ? ELSE tweet_text END,preview_url=CASE WHEN ?!='' THEN ? ELSE preview_url END WHERE id=?", (media.tweet_text,media.tweet_text,media.preview_url,media.preview_url,job_id))
            row = self.db.execute('SELECT tweet_id,author FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row:
                self.catalog.upsert(row['tweet_id'], row['author'], media.author_id, media.display_name, media.avatar_url)

    def tweet_rows(self, tweet_id):
        with self.lock:
            return [dict(r) for r in self.db.execute('SELECT * FROM jobs WHERE tweet_id=?', (tweet_id,))]

    def is_ignored(self, tweet_id):
        with self.lock:
            return self.db.execute('SELECT 1 FROM ignored_tweets WHERE tweet_id=?',(str(tweet_id),)).fetchone() is not None

    def ignore_tweets(self, ids, ignored=True):
        ids = list(set(str(i) for i in ids))
        if any(not i.isascii() or not i.isdigit() or len(i)>30 for i in ids):
            raise ValueError('无效推文 ID')
        with self.lock, self.db:
            if ignored:
                self.db.executemany('INSERT OR IGNORE INTO ignored_tweets(tweet_id,created) VALUES(?,?)',[(i,time.time()) for i in ids])
            else:
                self.db.executemany('DELETE FROM ignored_tweets WHERE tweet_id=?',[(i,) for i in ids])
            for tid in ids:self.ignore_versions[tid]=self.ignore_versions.get(tid,0)+1

    def ignore_snapshot(self):
        with self.lock:return dict(self.ignore_versions)

    def claim(self, job_id, force=False, baseline=None):
        # Ignore and start share this transaction/lock. A successful claim may finish.
        with self.lock, self.db:
            if baseline is not None:
                row=self.db.execute('SELECT tweet_id FROM jobs WHERE id=?',(job_id,)).fetchone()
                if not row or self.ignore_versions.get(row[0],0)!=baseline.get(row[0],0):return False
            cursor=self.db.execute("UPDATE jobs SET state='downloading',error='',force_download=CASE WHEN ? THEN 1 ELSE force_download END,updated=? WHERE id=? AND NOT EXISTS (SELECT 1 FROM ignored_tweets i WHERE i.tweet_id=jobs.tweet_id)",(int(force),time.time(),job_id))
            return cursor.rowcount==1

    def rows_for_tweets(self, ids):
        ids=list(set(str(i) for i in ids));rows=[]
        with self.lock:
            for start in range(0,len(ids),500):
                batch=ids[start:start+500]
                rows.extend(dict(r) for r in self.db.execute('SELECT * FROM jobs WHERE tweet_id IN ('+','.join('?' for _ in batch)+') ORDER BY id DESC',batch))
        return rows

    def tweet_count(self, ignored=False):
        with self.lock:
            if ignored:return self.db.execute('SELECT COUNT(*) FROM ignored_tweets').fetchone()[0]
            return self.db.execute("SELECT COUNT(DISTINCT tweet_id) FROM jobs WHERE state!='completed' AND NOT EXISTS (SELECT 1 FROM ignored_tweets i WHERE i.tweet_id=jobs.tweet_id)").fetchone()[0]

    def tweet_page(self, offset=0, limit=100, ignored=False):
        with self.lock:
            if ignored:
                ids=[r[0] for r in self.db.execute('SELECT tweet_id FROM ignored_tweets ORDER BY created DESC,tweet_id DESC LIMIT ? OFFSET ?',(limit,offset))]
            else:
                ids=[r[0] for r in self.db.execute("SELECT tweet_id FROM jobs WHERE state!='completed' AND NOT EXISTS (SELECT 1 FROM ignored_tweets i WHERE i.tweet_id=jobs.tweet_id) GROUP BY tweet_id ORDER BY MAX(id) DESC LIMIT ? OFFSET ?",(limit,offset))]
            grouped={i:[] for i in ids}
            for row in self.rows_for_tweets(ids):grouped[row['tweet_id']].append(row)
            return [grouped[i] for i in ids if grouped[i]]
