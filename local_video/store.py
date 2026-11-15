# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Persistent local queue, independent of the original Telegram archive."""
from dataclasses import asdict
from pathlib import Path
import sqlite3
import threading
import time


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=15)
        self.db.row_factory = sqlite3.Row
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

    def enqueue(self, video, output, quality):
        values = asdict(video)
        values.update(output=str(Path(output).resolve()), quality=quality, updated=time.time())
        with self.lock, self.db:
            self.db.execute('''INSERT INTO jobs(tweet_id,media_id,author,url,source,width,height,output,quality,updated)
                VALUES(:tweet_id,:media_id,:author,:url,:source,:width,:height,:output,:quality,:updated)
                ON CONFLICT(tweet_id,media_id,output,quality) DO UPDATE SET url=excluded.url, updated=excluded.updated,
                state=jobs.state''', values)
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
