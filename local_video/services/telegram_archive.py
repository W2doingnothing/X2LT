# SPDX-License-Identifier: GPL-2.0-only
"""Publish verified Telegram downloads through the shared media catalog."""
import json
from pathlib import Path
import re
import time

from ..local_collection import path_key
from ..migrations import backup_before_migration


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
            self._migrate_albums()

    def _migrate_albums(self):
        db = self._store.db
        albums = db.execute("""SELECT c.* FROM model_contents c JOIN model_sources s ON s.id=c.source_id
                              WHERE s.platform='telegram' AND c.native_id LIKE 'album:%'""").fetchall()
        if not albums:
            return
        backup_before_migration(db, 'telegram-message-archive-v1', require_jobs=False)
        queue_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='telegram_downloads'"
        ).fetchone()
        with db:
            for album in albums:
                media = db.execute('SELECT * FROM model_media WHERE content_id=?', (album['id'],)).fetchall()
                captions = {
                    item['id']: self._legacy_body(db, album['source_id'], item['id'],
                                                 item['native_id'].split(':', 1)[0], queue_exists)
                    for item in media
                }
                cards = db.execute('SELECT library,card FROM telegram_albums WHERE content=?',
                                   (album['id'],)).fetchall()
                for card in cards:
                    self._split_auto_album(db, album['id'], card, captions)
                for item in media:
                    message, separator, _ = item['native_id'].partition(':')
                    if not separator or not message.isdecimal():
                        continue
                    db.execute("""INSERT OR IGNORE INTO model_contents(source_id,native_id,author,text)
                                  VALUES(?,?,?,?)""", (album['source_id'], message, album['author'], captions[item['id']]))
                    content = db.execute('SELECT id FROM model_contents WHERE source_id=? AND native_id=?',
                                         (album['source_id'], message)).fetchone()[0]
                    db.execute("""UPDATE local_entries SET model_content=? WHERE model_content=?
                        AND id IN (SELECT t.entry FROM telegram_saved t
                                   JOIN model_variants v ON v.id=t.variant WHERE v.media_id=?)""",
                               (content, album['id'], item['id']))
                    self._move_legacy_media(db, item, content)
                if not db.execute('SELECT 1 FROM model_media WHERE content_id=?', (album['id'],)).fetchone():
                    db.execute('DELETE FROM telegram_albums WHERE content=?', (album['id'],))
                    db.execute('DELETE FROM model_contents WHERE id=?', (album['id'],))

    def _split_auto_album(self, db, content, card, captions):
        group = db.execute('SELECT * FROM local_groups WHERE id=? AND library=?',
                           (card['card'], card['library'])).fetchone()
        if not group:
            return
        rows = db.execute("""SELECT t.library,t.entry,t.message_order,e.title,m.id media FROM telegram_saved t
            JOIN model_variants v ON v.id=t.variant JOIN model_media m ON m.id=v.media_id
            JOIN local_entries e ON e.id=t.entry WHERE m.content_id=? ORDER BY t.model_location""",
                          (content,)).fetchall()
        in_library = [row for row in rows if row['library'] == card['library']]
        members = list(dict.fromkeys(row['entry'] for row in sorted(in_library, key=lambda row: row['message_order'])))
        if len(members) < 2 or members != self._groups.members(group['id']) or group['cover'] != members[0]:
            return
        # Recreate the old generated text in arrival order. Its title was fixed
        # when the second entry arrived, and its body was filled at most once.
        current_caption = body = ''
        title = None
        seen = set()
        for row in rows:
            current_caption = captions[row['media']] or current_caption
            if row['library'] != card['library'] or row['entry'] in seen:
                continue
            seen.add(row['entry'])
            if len(seen) == 2:
                title = next((line.strip()[:160] for line in current_caption.splitlines() if line.strip()),
                             row['title'][:160])
            if len(seen) >= 2 and not body:
                body = current_caption
        if group['title'] == title and group['body'] == body:
            self._groups.drop(group['id'])

    @staticmethod
    def _legacy_body(db, source, media, message, queue_exists):
        if queue_exists:
            row = db.execute("""SELECT d.payload FROM telegram_downloads d JOIN model_sources s
                ON s.scope=d.peer_id WHERE s.id=? AND d.message_id=? AND d.state='completed'
                ORDER BY d.rowid DESC LIMIT 1""", (source, message)).fetchone()
            if row:
                try:
                    return str(json.loads(row['payload']).get('body') or '')
                except (ValueError, AttributeError):
                    pass
        # Older saved media also copied its own message text onto the local entry.
        row = db.execute("""SELECT e.body FROM local_entries e JOIN telegram_saved t ON t.entry=e.id
            JOIN model_variants v ON v.id=t.variant WHERE v.media_id=? AND e.body!=''
            AND NOT EXISTS(SELECT 1 FROM telegram_saved other WHERE other.entry=e.id
                           AND other.message_order!=t.message_order)
            ORDER BY e.id LIMIT 1""", (media,)).fetchone()
        return row['body'] if row else ''

    @staticmethod
    def _move_legacy_media(db, item, content):
        destination = db.execute("""SELECT id FROM model_media WHERE content_id=?
            AND native_id=? AND media_type=?""", (content, item['native_id'], item['media_type'])).fetchone()
        if not destination:
            db.execute('UPDATE model_media SET content_id=? WHERE id=?', (content, item['id']))
            return
        # An item can already exist by message identity in another library.
        target = destination[0]
        variants = db.execute('SELECT * FROM model_variants WHERE media_id=?', (item['id'],)).fetchall()
        for variant in variants:
            existing = db.execute("""SELECT id FROM model_variants
                WHERE media_id=? AND quality=? AND extension=? AND width=? AND height=?""",
                (target, variant['quality'], variant['extension'], variant['width'], variant['height'])).fetchone()
            if not existing:
                db.execute('UPDATE model_variants SET media_id=? WHERE id=?', (target, variant['id']))
                continue
            duplicate = db.execute("""SELECT 1 FROM telegram_saved old JOIN telegram_saved current
                ON current.library=old.library WHERE old.variant=? AND current.variant=?""",
                (variant['id'], existing[0])).fetchone()
            if duplicate:
                continue
            for table, column in (('model_locations', 'variant_id'), ('model_tasks', 'variant_id'),
                                  ('telegram_saved', 'variant')):
                db.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?', (existing[0], variant['id']))
            db.execute('DELETE FROM model_variants WHERE id=?', (variant['id'],))
        if not db.execute('SELECT 1 FROM model_variants WHERE media_id=?', (item['id'],)).fetchone():
            db.execute('UPDATE OR IGNORE model_saved SET media_id=? WHERE media_id=?', (target, item['id']))
            db.execute('DELETE FROM model_saved WHERE media_id=?', (item['id'],))
            db.execute('DELETE FROM model_media WHERE id=?', (item['id'],))

    def register(self, job, path, digest):
        library = self._libraries.get(job['library_id'])
        path, root = Path(path), Path(library['path'])
        size = path.stat().st_size
        extension = job['extension'].lstrip('.')
        scope, message = job['peer_id'], job['message_id']
        model = self._store.model
        title = job['title'][:160]
        with self._store.lock:
            content = model.content('telegram', scope, message, job['peer_title'], job['body'])
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
                db.execute("""UPDATE local_entries SET body=? WHERE id=? AND body='' AND NOT text_edited""",
                           (job['body'], entry))
                self._save_metadata(db, content, job)
        return entry

    @staticmethod
    def _save_metadata(db, content, job):
        forward_id = job.get('forward_id') or ''
        forward_title = job.get('forward_title') or ''
        db.execute("""INSERT INTO telegram_messages(content,sent_at,forward_id,forward_title,forward_url,avatar_key)
            VALUES(?,?,?,?,?,?) ON CONFLICT(content) DO UPDATE SET
            sent_at=COALESCE(NULLIF(excluded.sent_at,0),sent_at),
            forward_id=COALESCE(NULLIF(excluded.forward_id,''),forward_id),
            forward_title=COALESCE(NULLIF(excluded.forward_title,''),forward_title),
            forward_url=CASE WHEN excluded.forward_id=forward_id
                THEN COALESCE(NULLIF(excluded.forward_url,''),forward_url)
                WHEN excluded.forward_id!='' THEN excluded.forward_url ELSE forward_url END,
            avatar_key=CASE WHEN excluded.forward_id=forward_id
                THEN COALESCE(NULLIF(excluded.avatar_key,''),avatar_key)
                WHEN excluded.forward_id!='' THEN excluded.avatar_key ELSE avatar_key END""",
            (content, job.get('sent_at') or 0, forward_id if forward_title else '',
             forward_title if forward_id else '', (job.get('forward_url') or '') if forward_id and forward_title else '',
             (job.get('avatar_key') or '') if forward_id and forward_title else ''))

    def preview_location(self, job):
        return self._collection.one("""SELECT t.entry AS id, l.id AS location_id
            FROM telegram_saved t JOIN local_memberships lm ON lm.library=t.library AND lm.entry=t.entry
            JOIN local_entries e ON e.id=t.entry JOIN local_locations l ON l.id=t.local_location AND l.entry=t.entry
            JOIN model_variants v ON v.id=t.variant JOIN model_media m ON m.id=v.media_id
            JOIN model_contents c ON c.id=m.content_id JOIN model_sources s ON s.id=c.source_id
            WHERE t.library=? AND s.platform='telegram' AND s.scope=? AND c.native_id=?
              AND m.native_id=? AND e.digest=? ORDER BY t.variant LIMIT 1""",
            (job['library_id'], job['peer_id'], job['message_id'],
             f"{job['message_id']}:{job['media_id']}", job['digest']))

    def open_source(self, library, entry, content_id=None):
        source = self._collection.one("""SELECT t.url FROM telegram_saved t JOIN local_memberships m
                 ON m.library=t.library AND m.entry=t.entry JOIN model_variants v ON v.id=t.variant
                 JOIN model_media mm ON mm.id=v.media_id WHERE t.library=? AND t.entry=?
                 AND (? IS NULL OR mm.content_id=?) ORDER BY t.message_order,t.variant LIMIT 1""",
                 (library, entry, content_id, content_id))
        url = source['url'] if source else ''
        if not re.fullmatch(r'https://t\.me/(?:c/\d+|[A-Za-z0-9_]+)/\d+', url):
            raise ValueError('找不到可打开的 Telegram 消息链接')
        return self._native.open_url(url)
