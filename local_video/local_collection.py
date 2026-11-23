# SPDX-License-Identifier: GPL-2.0-only
"""Local media identities, collection links and approved storage locations."""
import json
import os
from pathlib import Path
import time

from .catalog import normalize_tag
from .images import is_alias


def path_key(path):
    return os.path.normcase(str(path))


def file_signature(path):
    info = path.stat()
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


class LocalCollection:
    def __init__(self, store):
        self.store, self.db, self.lock = store, store.db, store.lock
        with self.lock:
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS local_entries(
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, digest TEXT NOT NULL,
                    title TEXT NOT NULL, extension TEXT NOT NULL, bytes INTEGER NOT NULL,
                    added REAL NOT NULL, model_content INTEGER,
                    pages TEXT NOT NULL DEFAULT '[]', page_order TEXT NOT NULL DEFAULT '[]',
                    cover TEXT, page INTEGER NOT NULL DEFAULT 0,
                    spread INTEGER NOT NULL DEFAULT 0, rtl INTEGER NOT NULL DEFAULT 0,
                    display_title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
                    UNIQUE(kind,digest)
                );
                CREATE TABLE IF NOT EXISTS local_locations(
                    id INTEGER PRIMARY KEY, entry INTEGER NOT NULL, path TEXT NOT NULL,
                    path_key TEXT NOT NULL UNIQUE, root TEXT NOT NULL, directory INTEGER NOT NULL,
                    managed INTEGER NOT NULL, owner TEXT, sig TEXT NOT NULL,
                    scanned INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS local_location_entry ON local_locations(entry);
                CREATE TABLE IF NOT EXISTS local_memberships(
                    library TEXT NOT NULL, entry INTEGER NOT NULL, PRIMARY KEY(library,entry)
                );
                CREATE TABLE IF NOT EXISTS local_tags(
                    entry INTEGER NOT NULL, normalized TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(entry,normalized)
                );
                CREATE INDEX IF NOT EXISTS local_tags_name ON local_tags(normalized,entry);
                CREATE TABLE IF NOT EXISTS local_scan_exclusions(
                    library TEXT NOT NULL, entry INTEGER NOT NULL, PRIMARY KEY(library,entry)
                );
            """)
            if 'scanned' not in {r['name'] for r in self.db.execute('PRAGMA table_info(local_locations)')}:
                self.db.execute('ALTER TABLE local_locations ADD COLUMN scanned INTEGER NOT NULL DEFAULT 0')
            columns = {row['name'] for row in self.db.execute('PRAGMA table_info(local_entries)')}
            for column in ('display_title','body'):
                if column not in columns:
                    self.db.execute(f"ALTER TABLE local_entries ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")

    def rows(self, sql, params=()):
        with self.lock:
            return [dict(row) for row in self.db.execute(sql, params)]

    def one(self, sql, params=()):
        with self.lock:
            row = self.db.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def entry(self, identifier):
        row = self.one('SELECT * FROM local_entries WHERE id=?', (identifier,))
        if not row:
            raise ValueError('找不到本地媒体')
        return row

    def known(self, path, kind, signature):
        row = self.one("""
            SELECT e.digest FROM local_locations l JOIN local_entries e ON e.id=l.entry
            WHERE l.path_key=? AND l.directory=0 AND l.sig=? AND e.kind=?
            """, (path_key(path), json.dumps(signature), kind))
        return row['digest'] if row else None

    def by_digest(self, kind, digest):
        return self.one('SELECT * FROM local_entries WHERE kind=? AND digest=?', (kind, digest))

    def add(self, item, digest, path, root, library, managed, scanned=False):
        model_content = None
        if item['kind'] != 'comic':
            model = self.store.model
            model_content = model.content('local', '', digest, text=item['title'])
            media = model.media(model_content, digest, item['kind'])
            model.variant(media, 'original', item['extension'])
        with self.lock, self.db:
            self.db.execute("""
                INSERT OR IGNORE INTO local_entries(kind,digest,title,extension,bytes,added,model_content,pages)
                VALUES(?,?,?,?,?,?,?,?)
                """, (item['kind'], digest, item['title'], item['extension'], item['bytes'], time.time(),
                       model_content, json.dumps(item['pages'], ensure_ascii=False)))
            entry = self.one('SELECT id FROM local_entries WHERE kind=? AND digest=?', (item['kind'], digest))['id']
            if scanned and self.excluded(library,entry):
                return entry
            moved = None
            if scanned and not self.one('SELECT 1 FROM local_locations WHERE path_key=?', (path_key(path),)):
                missing = [row for row in self.location_rows(entry)
                           if row['scanned'] and row['owner'] == library and self.state(row) == 'missing']
                if len(missing) == 1:
                    moved = missing[0]['id']
            self.put_location(entry, path, root, library if managed else None, moved, scanned)
            self.db.execute('INSERT OR IGNORE INTO local_memberships VALUES(?,?)', (library, entry))
            if not scanned:
                self.db.execute('DELETE FROM local_scan_exclusions WHERE library=? AND entry=?', (library, entry))
        return entry

    def put_location(self, entry, path, root, owner, current=None, scanned=False):
        signature = json.dumps(file_signature(path))
        directory = int(path.is_dir())
        if current is not None:
            self.db.execute("""
                UPDATE local_locations SET path=?,path_key=?,root=?,directory=?,managed=?,owner=?,sig=?,scanned=?
                WHERE id=? AND entry=?
                """, (str(path), path_key(path), str(root), directory, int(owner is not None),
                       owner, signature, int(scanned), current, entry))
            return
        previous = self.one('SELECT id,entry FROM local_locations WHERE path_key=?', (path_key(path),))
        if previous and previous['entry'] != entry:
            self.db.execute('UPDATE local_locations SET path_key=?,managed=0,owner=NULL WHERE id=?',
                            (path_key(path) + '\x00' + str(previous['id']), previous['id']))
        self.db.execute("""
            INSERT INTO local_locations(entry,path,path_key,root,directory,managed,owner,sig,scanned)
            VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(path_key) DO UPDATE SET entry=excluded.entry,path=excluded.path,root=excluded.root,
                directory=excluded.directory,sig=excluded.sig,
                managed=MAX(local_locations.managed,excluded.managed),
                owner=COALESCE(local_locations.owner,excluded.owner),
                scanned=CASE WHEN local_locations.managed THEN local_locations.scanned ELSE excluded.scanned END
            """, (entry, str(path), path_key(path), str(root), directory, int(owner is not None), owner, signature,
                  int(scanned)))

    def location_rows(self, entry):
        return self.rows('SELECT * FROM local_locations WHERE entry=? ORDER BY managed DESC,id', (entry,))

    @staticmethod
    def state(row):
        path, root = Path(row['path']), Path(row['root'])
        try:
            if row['path_key'] != path_key(path):
                return 'missing'
            anchor = Path(root.anchor)
            if not anchor.exists():
                return 'offline'
            if not root.exists() or not path.exists():
                return 'missing'
            if (path != path.resolve(strict=True) or is_alias(path) or path.is_dir() != bool(row['directory'])
                    or (path != root and root not in path.parents)):
                return 'missing'
            if not row['directory'] and json.dumps(file_signature(path)) != row['sig']:
                return 'missing'
            return 'available'
        except OSError:
            return 'offline'

    def get_location(self, entry, location=None):
        rows = self.location_rows(entry)
        if location is not None:
            rows = [row for row in rows if row['id'] == location]
        return next((row for row in rows if self.state(row) == 'available'), None)

    def set_tags(self, entry, names):
        self.entry(entry)
        tags = dict(normalize_tag(name) for name in names)
        with self.lock, self.db:
            self.db.execute('DELETE FROM local_tags WHERE entry=?', (entry,))
            self.db.executemany('INSERT INTO local_tags VALUES(?,?,?)',
                                ((entry, key, name) for key, name in tags.items()))

    def set_link(self, entry, library, linked):
        self.entry(entry)
        with self.lock, self.db:
            if linked:
                self.db.execute('INSERT OR IGNORE INTO local_memberships VALUES(?,?)', (library, entry))
                self.db.execute('DELETE FROM local_scan_exclusions WHERE library=? AND entry=?', (library, entry))
            else:
                self.db.execute('DELETE FROM local_memberships WHERE library=? AND entry=?', (library, entry))

    def exclude(self, library, entry):
        with self.lock, self.db:
            self.db.execute('INSERT OR IGNORE INTO local_scan_exclusions VALUES(?,?)', (library, entry))
            self.db.execute('DELETE FROM local_memberships WHERE library=? AND entry=?', (library, entry))

    def excluded(self, library, entry):
        return self.one('SELECT 1 FROM local_scan_exclusions WHERE library=? AND entry=?', (library, entry)) is not None

    def exclusions(self, library):
        return self.rows("""SELECT e.id,e.title,e.kind FROM local_scan_exclusions x
                            JOIN local_entries e ON e.id=x.entry WHERE x.library=? ORDER BY e.title""", (library,))

    def restore(self, library, entry):
        with self.lock, self.db:
            self.db.execute('DELETE FROM local_scan_exclusions WHERE library=? AND entry=?', (library, entry))

    def tag_options(self, library):
        return self.rows("""
            SELECT MIN(t.name) name,COUNT(*) count FROM local_tags t
            JOIN local_memberships m ON m.entry=t.entry
            WHERE m.library=? GROUP BY t.normalized ORDER BY t.normalized
            """, (library,))

    def rename_tag(self, old, new):
        old_key = normalize_tag(old)[0]
        key, name = normalize_tag(new)
        with self.lock, self.db:
            if old_key == key:
                self.db.execute('UPDATE local_tags SET name=? WHERE normalized=?', (name, key))
            else:
                self.db.execute('INSERT OR IGNORE INTO local_tags SELECT entry,?,? FROM local_tags WHERE normalized=?',
                                (key, name, old_key))
                self.db.execute('DELETE FROM local_tags WHERE normalized=?', (old_key,))

    def delete_tag(self, name):
        key = normalize_tag(name)[0]
        with self.lock, self.db:
            self.db.execute('DELETE FROM local_tags WHERE normalized=?', (key,))

    def stats(self):
        return self.rows("""
            SELECT m.library,COUNT(*) message_count,COUNT(*) media_count,
                SUM(e.kind='image') image_count,SUM(e.kind='video') video_count,
                SUM(e.kind='comic') comic_count
            FROM local_memberships m JOIN local_entries e ON e.id=m.entry GROUP BY m.library
            """)

    def edit_text(self, library, identifier, title, body):
        title = title.strip()
        if len(title) > 160 or len(body) > 8000 or any(char in title for char in '\r\n\0'):
            raise ValueError('名称或正文格式无效')
        with self.lock, self.db:
            cursor = self.db.execute("""
                UPDATE local_entries SET display_title=?,body=? WHERE id=?
                AND EXISTS (SELECT 1 FROM local_memberships WHERE library=? AND entry=local_entries.id)
                """, (title,body,identifier,library))
            if not cursor.rowcount:
                raise ValueError('找不到当前媒体库的本地媒体')

    def filters(self, query, tags, untagged, grouped=False):
        where = ''
        args = []
        for word in query[:200].split():
            where += """ AND (instr(lower(e.title),lower(?)) OR instr(lower(e.display_title),lower(?))
                OR instr(lower(e.body),lower(?)) OR EXISTS(
                SELECT 1 FROM local_locations l WHERE l.entry=e.id AND instr(lower(l.path),lower(?)))"""
            args.extend((word,word,word,word))
            if grouped:
                where += ' OR instr(lower(g.title),lower(?)) OR instr(lower(g.body),lower(?))'
                args.extend((word,word))
            where += ')'
        if untagged:
            where += ' AND NOT EXISTS(SELECT 1 FROM local_tags t WHERE t.entry=e.id)'
        for tag in tags:
            where += ' AND EXISTS(SELECT 1 FROM local_tags t WHERE t.entry=e.id AND t.normalized=?)'
            args.append(normalize_tag(tag)[0])
        return where, args

    def describe(self, rows, library):
        for row in rows:
            identifier = row['id']
            row['title'] = row.pop('display_title') or row['title']
            row['tags'] = [t['name'] for t in self.rows(
                'SELECT name FROM local_tags WHERE entry=? ORDER BY normalized', (identifier,))]
            row['libraries'] = [m['library'] for m in self.rows(
                'SELECT library FROM local_memberships WHERE entry=? ORDER BY library', (identifier,))]
            row['locations'] = []
            for location in self.location_rows(identifier):
                state = self.state(location)
                owned = location['managed'] and location['owner'] == library
                row['locations'].append(dict(
                    id=location['id'], state=state, managed=bool(location['managed']),
                    manageable=bool(owned and state == 'available' and Path(location['path']) != Path(location['root'])),
                    recyclable=bool(owned and state == 'available' and not location['directory']),
                    label=Path(location['path']).name))
        return rows

    def page(self, library, query, tags, untagged, offset, limit):
        filters, args = self.filters(query, tags, untagged)
        base = ' FROM local_entries e JOIN local_memberships m ON m.entry=e.id WHERE m.library=?' + filters
        args = [library, *args]
        total = self.one('SELECT COUNT(*) n' + base, args)['n']
        limit = min(60, max(1, limit))
        offset = min(max(0, offset), max(0, (total-1)//limit)*limit)
        rows = self.rows('SELECT e.id,e.title,e.display_title,e.body,e.kind,e.bytes,e.added' + base +
                         ' ORDER BY e.added DESC,e.id DESC LIMIT ? OFFSET ?', [*args, limit, offset])
        return dict(items=self.describe(rows, library), total=total, offset=offset)
