# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 W2doingnothing
"""Rebuildable message projection; jobs remain the download authority."""
from pathlib import Path
import unicodedata


MEDIA_FIELDS = ('id', 'media_id', 'media_type', 'extension', 'state', 'error',
                'bytes', 'width', 'height', 'quality', 'output', 'verification')


def normalize_tag(value):
    if not isinstance(value, str):
        raise ValueError('标签必须是文字')
    name = unicodedata.normalize('NFKC', value)
    if name.startswith('#'):
        name = name[1:]
    if not 1 <= len(name) <= 32 or any(
        c not in '_-' and not unicodedata.category(c).startswith(('L', 'N')) for c in name
    ):
        raise ValueError('标签须为 1–32 个字母、数字、下划线或连字符')
    return name.casefold(), name


class Catalog:
    def __init__(self, db, lock, defer=False):
        self.db, self.lock = db, lock
        self._ready = False
        # Additive DDL only: no rewriting jobs, paths, or existing history.
        with lock, db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS catalog_messages (
                    platform TEXT NOT NULL, message_id TEXT NOT NULL,
                    author_key TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(platform,message_id));
                CREATE TABLE IF NOT EXISTS catalog_authors (
                    platform TEXT NOT NULL, author_key TEXT NOT NULL,
                    author_id TEXT NOT NULL DEFAULT '', handle TEXT NOT NULL DEFAULT '',
                    display_name TEXT NOT NULL DEFAULT '', avatar_url TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(platform,author_key));
                CREATE TABLE IF NOT EXISTS catalog_tags (
                    normalized TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS catalog_message_tags (
                    platform TEXT NOT NULL, message_id TEXT NOT NULL, tag TEXT NOT NULL,
                    PRIMARY KEY(platform,message_id,tag));
                CREATE INDEX IF NOT EXISTS catalog_tag_messages ON catalog_message_tags(tag,platform,message_id);
                CREATE INDEX IF NOT EXISTS catalog_author_messages ON catalog_messages(platform,author_key,message_id);
                CREATE TABLE IF NOT EXISTS catalog_migrations (name TEXT PRIMARY KEY, cursor INTEGER NOT NULL);
            ''')
        if not defer:
            self.ensure_ready()

    def ensure_ready(self):
        """Finish startup projection once; a failed migration remains retryable."""
        with self.lock:
            if not self._ready:
                self._backfill()
                self._ready = True

    def _backfill(self):
        with self.lock:
            row = self.db.execute("SELECT cursor FROM catalog_migrations WHERE name='jobs-v1'").fetchone()
            cursor = row[0] if row else 0
            if not self.db.execute('SELECT 1 FROM catalog_messages LIMIT 1').fetchone():
                cursor = 0
            while True:
                rows = self.db.execute('SELECT id,tweet_id,author FROM jobs WHERE id>? ORDER BY id LIMIT 500', (cursor,)).fetchall()
                if not rows:
                    break
                with self.db:
                    for row in rows:
                        self.upsert(row['tweet_id'], row['author'])
                    cursor = rows[-1]['id']
                    self.db.execute("INSERT INTO catalog_migrations VALUES('jobs-v1',?) ON CONFLICT(name) DO UPDATE SET cursor=excluded.cursor", (cursor,))

    def upsert(self, message_id, handle, author_id='', display_name='', avatar_url='', platform='x'):
        """Called within the Store write transaction and its shared lock."""
        message_id, handle, author_id = str(message_id), str(handle), str(author_id or '')
        old = self.db.execute('SELECT author_key FROM catalog_messages WHERE platform=? AND message_id=?', (platform, message_id)).fetchone()
        key = 'id:' + author_id if author_id else (old[0] if old else 'handle:' + unicodedata.normalize('NFKC', handle).casefold() if handle else 'message:' + message_id)
        previous = self.author(message_id, platform) if old else {}
        values = (platform, key, author_id or previous.get('author_id', ''), handle,
                  str(display_name or previous.get('display_name', '')), str(avatar_url or previous.get('avatar_url', '')))
        self.db.execute('''INSERT INTO catalog_authors VALUES(?,?,?,?,?,?)
            ON CONFLICT(platform,author_key) DO UPDATE SET
            author_id=CASE WHEN excluded.author_id!='' THEN excluded.author_id ELSE author_id END,
            handle=excluded.handle,
            display_name=CASE WHEN excluded.display_name!='' THEN excluded.display_name ELSE display_name END,
            avatar_url=CASE WHEN excluded.avatar_url!='' THEN excluded.avatar_url ELSE avatar_url END''', values)
        self.db.execute('INSERT INTO catalog_messages VALUES(?,?,?) ON CONFLICT(platform,message_id) DO UPDATE SET author_key=excluded.author_key', (platform, message_id, key))

    def author(self, message_id, platform='x'):
        with self.lock:
            row = self.db.execute('''SELECT a.* FROM catalog_messages m JOIN catalog_authors a
                ON a.platform=m.platform AND a.author_key=m.author_key
                WHERE m.platform=? AND m.message_id=?''', (platform, str(message_id))).fetchone()
            return dict(row) if row else {}

    def set_tags(self, message_id, tags, platform='x'):
        if not isinstance(tags, (list, tuple)) or len(tags) > 20:
            raise ValueError('每条消息最多 20 个标签')
        normalized = dict(normalize_tag(t) for t in reversed(tags))
        with self.lock, self.db:
            if not self.db.execute('SELECT 1 FROM catalog_messages WHERE platform=? AND message_id=?', (platform, str(message_id))).fetchone():
                raise ValueError('消息不存在')
            self.db.execute('DELETE FROM catalog_message_tags WHERE platform=? AND message_id=?', (platform, str(message_id)))
            for key, name in normalized.items():
                self.db.execute('INSERT OR IGNORE INTO catalog_tags VALUES(?,?)', (key, name))
                self.db.execute('INSERT INTO catalog_message_tags VALUES(?,?,?)', (platform, str(message_id), key))

    def rename_tag(self, old, new):
        oldkey, _ = normalize_tag(old)
        key, name = normalize_tag(new)
        with self.lock, self.db:
            if not self.db.execute('SELECT 1 FROM catalog_tags WHERE normalized=?', (oldkey,)).fetchone():
                raise ValueError('标签不存在')
            if oldkey == key:
                self.db.execute('UPDATE catalog_tags SET name=? WHERE normalized=?', (name, key))
            else:
                self.db.execute('INSERT OR IGNORE INTO catalog_tags VALUES(?,?)', (key, name))
                self.db.execute('INSERT OR IGNORE INTO catalog_message_tags SELECT platform,message_id,? FROM catalog_message_tags WHERE tag=?', (key, oldkey))
                self.db.execute('DELETE FROM catalog_message_tags WHERE tag=?', (oldkey,))
                self.db.execute('DELETE FROM catalog_tags WHERE normalized=?', (oldkey,))

    def delete_tag(self, name):
        key, _ = normalize_tag(name)
        with self.lock, self.db:
            self.db.execute('DELETE FROM catalog_message_tags WHERE tag=?', (key,))
            self.db.execute('DELETE FROM catalog_tags WHERE normalized=?', (key,))

    def tags(self, output=None, saved_only=False):
        args = []
        scope = ''
        if output is not None:
            scope = ' AND j.output=?'
            args.append(str(Path(output).resolve()))
        if saved_only:
            scope += " AND j.state='completed'"
        visibility = '' if saved_only else ' AND NOT EXISTS(SELECT 1 FROM ignored_tweets i WHERE i.tweet_id=mt.message_id)'
        with self.lock:
            return [dict(r) for r in self.db.execute('''SELECT t.name,COUNT(*) count FROM catalog_tags t
                JOIN catalog_message_tags mt ON mt.tag=t.normalized WHERE mt.platform='x'
                AND EXISTS(SELECT 1 FROM jobs j WHERE j.tweet_id=mt.message_id''' + scope + ''')
                ''' + visibility + ''' GROUP BY t.normalized ORDER BY t.normalized''', args)]

    def library_counts(self, output):
        with self.lock:
            row = self.db.execute('''SELECT COUNT(DISTINCT tweet_id), COUNT(*) FROM (
                SELECT DISTINCT tweet_id,media_type,media_id FROM jobs WHERE output=? AND state='completed')''', (str(Path(output).resolve()),)).fetchone()
            return {'message_count': row[0], 'media_count': row[1]}

    def authors_page(self, output, query='', tags=None, untagged=False, offset=0, limit=50):
        """Database-only aggregation, scoped to saved media, never private media I/O."""
        if not isinstance(tags or [], (list, tuple)) or len(tags or []) > 20:
            raise ValueError('标签筛选无效')
        clauses = ["j.output=?", "j.state='completed'"]
        args = [str(Path(output).resolve())]
        required = [normalize_tag(t)[0] for t in (tags or [])]
        for word in str(query)[:200].split():
            if unicodedata.normalize('NFKC', word).startswith('#'):
                required.append(normalize_tag(word)[0])
            else:
                clauses.append("(a.handle LIKE ? ESCAPE '\\' OR a.display_name LIKE ? ESCAPE '\\')")
                escaped = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                args.extend(['%' + escaped + '%'] * 2)
        for tag in set(required):
            clauses.append("EXISTS(SELECT 1 FROM catalog_message_tags mt WHERE mt.platform='x' AND mt.message_id=j.tweet_id AND mt.tag=?)")
            args.append(tag)
        if untagged:
            clauses.append("NOT EXISTS(SELECT 1 FROM catalog_message_tags mt WHERE mt.platform='x' AND mt.message_id=j.tweet_id)")
        cte = '''WITH saved AS (
            SELECT DISTINCT m.author_key key,j.tweet_id,j.media_type,j.media_id,
                a.handle,a.display_name,(a.avatar_url!='') avatar_available
            FROM jobs j JOIN catalog_messages m ON m.platform='x' AND m.message_id=j.tweet_id
            JOIN catalog_authors a ON a.platform=m.platform AND a.author_key=m.author_key
            WHERE ''' + ' AND '.join(clauses) + '''), authors AS (
            SELECT key,handle,display_name,MAX(avatar_available) avatar_available,
                COUNT(*) media_count,COUNT(DISTINCT tweet_id) message_count,MIN(tweet_id) message_id
            FROM saved GROUP BY key) '''
        offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
        with self.lock:
            total = self.db.execute(cte + 'SELECT COUNT(*) FROM authors', args).fetchone()[0]
            rows = self.db.execute(cte + 'SELECT * FROM authors ORDER BY media_count DESC,message_count DESC,key LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
            return {'authors': [{**dict(row), 'avatar_available': bool(row['avatar_available'])} for row in rows], 'total': total, 'offset': offset}

    def page(self, query='', filter='all', output=None, tags=None, untagged=False, offset=0, limit=50, saved_only=False, author_key=''):
        conditions = {'all': 'g.ignored=0', 'pending': 'g.ignored=0 AND g.complete=0',
                      'completed': 'g.ignored=0 AND g.archived=1' if output is not None else 'g.ignored=0 AND g.complete=1',
                      'ignored': 'g.ignored=1'}
        if filter not in conditions:
            raise ValueError('筛选无效')
        if saved_only:
            if output is None:
                raise ValueError('归档浏览需要媒体库')
            conditions = {'all': '1', 'pending': '0', 'completed': '1', 'ignored': 'g.ignored=1'}
        offset, limit = max(0, int(offset)), min(100, max(1, int(limit)))
        if tags is not None and (not isinstance(tags, (list, tuple)) or len(tags) > 20):
            raise ValueError('标签筛选无效')
        required = [normalize_tag(t)[0] for t in (tags or [])]
        words = []
        for word in str(query)[:200].split():
            if unicodedata.normalize('NFKC', word).startswith('#'):
                required.append(normalize_tag(word)[0])
            else:
                words.append(word)
        args = []
        scope = ''
        if output is not None:
            scope = ' WHERE output=?'
            args.append(str(Path(output).resolve()))
        if saved_only:
            scope += " AND state='completed'"
        cte = '''WITH scoped AS (SELECT * FROM jobs''' + scope + '''), grouped AS (
            SELECT tweet_id,MAX(id) newest,MIN(state='completed') complete,MAX(state='completed') archived,
            EXISTS(SELECT 1 FROM ignored_tweets i WHERE i.tweet_id=scoped.tweet_id) ignored
            FROM scoped GROUP BY tweet_id) '''
        clauses = []
        if author_key:
            clauses.append("EXISTS(SELECT 1 FROM catalog_messages m WHERE m.platform='x' AND m.message_id=g.tweet_id AND m.author_key=?)")
            args.append(str(author_key)[:250])
        for word in words:
            clauses.append("(EXISTS(SELECT 1 FROM scoped j WHERE j.tweet_id=g.tweet_id AND (j.tweet_text LIKE ? ESCAPE '\\' OR j.author LIKE ? ESCAPE '\\')) OR EXISTS(SELECT 1 FROM catalog_messages m JOIN catalog_authors a ON a.platform=m.platform AND a.author_key=m.author_key WHERE m.platform='x' AND m.message_id=g.tweet_id AND (a.display_name LIKE ? ESCAPE '\\' OR a.handle LIKE ? ESCAPE '\\')))")
            escaped = word.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            args.extend(['%' + escaped + '%'] * 4)
        for key in set(required):
            clauses.append("EXISTS(SELECT 1 FROM catalog_message_tags mt WHERE mt.platform='x' AND mt.message_id=g.tweet_id AND mt.tag=?)")
            args.append(key)
        if untagged:
            clauses.append("NOT EXISTS(SELECT 1 FROM catalog_message_tags mt WHERE mt.platform='x' AND mt.message_id=g.tweet_id)")
        search = ' AND '.join(clauses) or '1'
        with self.lock:
            sums = ','.join('SUM(CASE WHEN ' + c + ' THEN 1 ELSE 0 END)' for c in conditions.values())
            row = self.db.execute(cte + 'SELECT ' + sums + ' FROM grouped g WHERE ' + search, args).fetchone()
            counts = {key: int(row[i] or 0) for i, key in enumerate(conditions)}
            page = self.db.execute(cte + 'SELECT * FROM grouped g WHERE (' + conditions[filter] + ') AND ' + search + ' ORDER BY newest DESC LIMIT ? OFFSET ?', [*args, limit, offset]).fetchall()
            by_message = {}
            authors, labels = {}, {}
            ids = [group['tweet_id'] for group in page]
            if ids:
                placeholders = ','.join('?' for _ in ids)
                rowargs = list(ids)
                rowscope = ''
                if output is not None:
                    rowscope = ' AND output=?'
                    rowargs.append(str(Path(output).resolve()))
                if saved_only:
                    rowscope += " AND state='completed'"
                for media in self.db.execute('SELECT * FROM jobs WHERE tweet_id IN (' + placeholders + ')' + rowscope + ' ORDER BY id DESC', rowargs):
                    by_message.setdefault(media['tweet_id'], []).append(media)
                for author in self.db.execute('''SELECT m.message_id,a.handle,a.display_name,a.avatar_url
                    FROM catalog_messages m JOIN catalog_authors a
                    ON a.platform=m.platform AND a.author_key=m.author_key
                    WHERE m.platform='x' AND m.message_id IN (''' + placeholders + ')', ids):
                    authors[author['message_id']] = dict(author)
                for tag in self.db.execute('''SELECT mt.message_id,t.name FROM catalog_tags t
                    JOIN catalog_message_tags mt ON mt.tag=t.normalized
                    WHERE mt.platform='x' AND mt.message_id IN (''' + placeholders + ') ORDER BY t.normalized', ids):
                    labels.setdefault(tag['message_id'], []).append(tag['name'])
            tasks = []
            for group in page:
                rows = by_message[group['tweet_id']]
                head = rows[0]
                author = authors.get(group['tweet_id'], {})
                tasks.append({'id': group['tweet_id'], 'platform': 'x', 'author': head['author'],
                    'handle': author.get('handle') or head['author'], 'display_name': author.get('display_name') or head['author'],
                    'avatar_available': bool(author.get('avatar_url')), 'tags': labels.get(group['tweet_id'], []),
                    'title': next((s.strip() for s in head['tweet_text'].splitlines() if s.strip()), '推文 ' + group['tweet_id']),
                    'text': head['tweet_text'], 'ignored': bool(group['ignored']),
                    'updated': max(r['updated'] or 0 for r in rows),
                    'media': [{key: r[key] for key in MEDIA_FIELDS} for r in rows]})
            return {'tasks': tasks, 'counts': counts, 'total': counts[filter], 'offset': offset}
