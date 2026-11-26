# SPDX-License-Identifier: GPL-2.0-only
"""Publish verified Telegram downloads through the shared media catalog."""
from pathlib import Path
import re
import time

from ..local_collection import path_key


class TelegramArchive:
    def __init__(self, store, libraries, collection, groups, native):
        self._store, self._libraries = store, libraries
        self._collection, self._groups, self._native = collection, groups, native
        with store.lock:
            store.db.executescript("""
                CREATE TABLE IF NOT EXISTS telegram_albums(
                    library TEXT NOT NULL, content INTEGER NOT NULL, card INTEGER NOT NULL,
                    PRIMARY KEY(library,content)
                );
                CREATE TRIGGER IF NOT EXISTS telegram_location_moved AFTER UPDATE OF path ON local_locations
                BEGIN
                    UPDATE model_locations SET local_path=NEW.path
                    WHERE id IN (SELECT model_location FROM telegram_saved WHERE local_location=NEW.id);
                END;
                CREATE TRIGGER IF NOT EXISTS telegram_location_deleted AFTER DELETE ON local_locations
                BEGIN
                    UPDATE model_locations SET state='removed'
                    WHERE id IN (SELECT model_location FROM telegram_saved WHERE local_location=OLD.id);
                    UPDATE telegram_saved SET local_location=0 WHERE local_location=OLD.id;
                END;
                CREATE TRIGGER IF NOT EXISTS telegram_membership_removed AFTER DELETE ON local_memberships
                BEGIN
                    UPDATE model_locations SET state='removed'
                    WHERE id IN (SELECT model_location FROM telegram_saved WHERE library=OLD.library AND entry=OLD.entry);
                END;
                CREATE TRIGGER IF NOT EXISTS telegram_membership_restored AFTER INSERT ON local_memberships
                BEGIN
                    UPDATE model_locations SET state='completed'
                    WHERE id IN (SELECT model_location FROM telegram_saved t JOIN local_locations l
                                 ON l.id=t.local_location WHERE t.library=NEW.library AND t.entry=NEW.entry);
                END;
            """)

    def register(self, job, path, digest):
        library = self._libraries.get(job['library_id'])
        path, root = Path(path), Path(library['path'])
        size = path.stat().st_size
        extension = job['extension'].lstrip('.')
        scope, message = job['peer_id'], job['message_id']
        native_id = 'album:' + job['group_id'] if job['group_id'] else message
        model = self._store.model
        title = job['title'][:160]
        with self._store.lock:
            content = model.content('telegram', scope, native_id, job['peer_title'], job['body'])
            media = model.media(content, message + ':' + job['media_id'], job['kind'])
            variant = model.variant(media, 'original', extension, job['width'], job['height'])
            entry = self._collection.add(
                dict(kind=job['kind'], title=title, extension=extension, bytes=size, pages=[]),
                digest, path, root, library['id'], True, model_content=content,
            )
            with self._store.db as db:
                local_location = db.execute('SELECT id FROM local_locations WHERE path_key=?',
                                            (path_key(path),)).fetchone()[0]
                saved = db.execute('SELECT model_location FROM telegram_saved WHERE library=? AND variant=?',
                                   (library['id'], variant)).fetchone()
                location = (str(root), str(path), size, digest, 'length+sha256' if job['size'] else 'sha256')
                if saved:
                    model_location = saved[0]
                    db.execute("""UPDATE model_locations SET output=?,local_path=?,bytes=?,sha256=?,
                                  verification=?,state='completed' WHERE id=?""", (*location, model_location))
                else:
                    model_location = db.execute("""INSERT INTO model_locations
                        (output,local_path,bytes,sha256,verification,variant_id,state)
                        VALUES(?,?,?,?,?,?,'completed')""", (*location, variant)).lastrowid
                db.execute("""INSERT OR REPLACE INTO telegram_saved
                    (library,variant,entry,local_location,model_location,message_order,url)
                    VALUES(?,?,?,?,?,?,?)""",
                    (library['id'], variant, entry, local_location, model_location, int(message), job['url']))
                db.execute('UPDATE model_sources SET label=? WHERE platform=? AND scope=?',
                           (job['peer_title'], 'telegram', scope))
                db.execute('INSERT OR IGNORE INTO model_saved(media_id,added_at) VALUES(?,?)', (media, time.time()))
                db.execute("UPDATE local_entries SET body=? WHERE id=? AND body=''", (job['body'], entry))
                if job['group_id']:
                    self._group(db, library['id'], content, title)
        return entry

    def _group(self, db, library, content, title):
        rows = db.execute("""
            SELECT t.entry,gi.group_id FROM telegram_saved t JOIN model_variants v ON v.id=t.variant
            JOIN model_media m ON m.id=v.media_id JOIN local_memberships s ON s.entry=t.entry AND s.library=t.library
            LEFT JOIN local_group_items gi ON gi.entry=t.entry AND gi.library=t.library
            WHERE t.library=? AND m.content_id=? ORDER BY t.message_order
        """, (library, content)).fetchall()
        album = db.execute('SELECT card FROM telegram_albums WHERE library=? AND content=?',
                           (library, content)).fetchone()
        card = album[0] if album else None
        if card and not db.execute('SELECT 1 FROM local_groups WHERE id=?', (card,)).fetchone():
            return
        members = list(dict.fromkeys(row['entry'] for row in rows if row['group_id'] in (None, card)))
        if len(members) < 2:
            return
        body = db.execute('SELECT text FROM model_contents WHERE id=?', (content,)).fetchone()[0]
        if card:
            current = self._groups.members(card)
            source_order = [entry for entry in members if entry in current]
            if current != source_order:
                # Preserve an order or membership edited in library management.
                members = list(dict.fromkeys([*current, *members]))
            if members == current:
                return
            if body:
                db.execute("UPDATE local_groups SET body=? WHERE id=? AND body=''", (body, card))
            if current == source_order:
                db.execute('UPDATE local_groups SET cover=? WHERE id=? AND cover=?',
                           (members[0], card, current[0]))
        else:
            title = next((line.strip()[:160] for line in body.splitlines() if line.strip()), title)
            card = db.execute('INSERT INTO local_groups(library,title,cover,body) VALUES(?,?,?,?)',
                              (library, title, members[0], body)).lastrowid
            db.execute('INSERT INTO telegram_albums VALUES(?,?,?)', (library, content, card))
        self._groups.save_members(library, card, members[:200])

    def open_source(self, library, entry):
        source = self._collection.one("""SELECT t.url FROM telegram_saved t JOIN local_memberships m
                 ON m.library=t.library AND m.entry=t.entry WHERE t.library=? AND t.entry=?
                 ORDER BY t.message_order LIMIT 1""", (library, entry))
        url = source['url'] if source else ''
        if not re.fullmatch(r'https://t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+', url):
            raise ValueError('找不到可打开的 Telegram 消息链接')
        return self._native.open_url(url)
