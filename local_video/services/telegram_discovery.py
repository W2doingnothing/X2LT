# SPDX-License-Identifier: GPL-2.0-only
"""Durable Telegram history scan checkpoints and discovered media."""
import json


class TelegramDiscovery:
    def __init__(self, store):
        self._store = store
        with store.lock, store.db:
            store.db.execute("""CREATE TABLE IF NOT EXISTS telegram_scan_cursors (
                account INTEGER NOT NULL, source TEXT NOT NULL, completed INTEGER NOT NULL DEFAULT 0,
                upper_id INTEGER, next_before INTEGER NOT NULL DEFAULT 0, remaining INTEGER, start_at REAL,
                PRIMARY KEY (account, source)
            )""")
            store.db.execute("""CREATE TABLE IF NOT EXISTS telegram_discoveries (
                account INTEGER NOT NULL, source TEXT NOT NULL, message_id INTEGER NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY (account, source, message_id)
            )""")
            store.db.execute("""CREATE TABLE IF NOT EXISTS telegram_backfill (
                id INTEGER PRIMARY KEY CHECK(id=1), request TEXT NOT NULL
            )""")

    def backfill_request(self):
        with self._store.lock:
            row = self._store.db.execute('SELECT request FROM telegram_backfill WHERE id=1').fetchone()
        return json.loads(row[0]) if row else None

    def save_backfill(self, request):
        with self._store.lock, self._store.db as db:
            if request is None:
                db.execute('DELETE FROM telegram_backfill WHERE id=1')
            else:
                db.execute('REPLACE INTO telegram_backfill VALUES (1, ?)', (json.dumps(request),))

    def scoped(self, namespace):
        return _ScopedDiscovery(self, namespace)

    def cursor(self, key):
        with self._store.lock:
            row = self._store.db.execute('SELECT * FROM telegram_scan_cursors WHERE account=? AND source=?',
                                         key).fetchone()
        return dict(row) if row else None

    def begin(self, key, upper, remaining, start_at):
        with self._store.lock, self._store.db as db:
            db.execute("""INSERT INTO telegram_scan_cursors (account, source, upper_id, remaining, start_at)
                VALUES (?, ?, ?, ?, ?) ON CONFLICT (account, source) DO UPDATE SET
                upper_id=excluded.upper_id, next_before=0, remaining=excluded.remaining, start_at=excluded.start_at
                WHERE upper_id IS NULL""", (*key, upper, remaining, start_at))
        return self.cursor(key)

    def record(self, key, message_id, remaining, item):
        with self._store.lock, self._store.db as db:
            if item is not None:
                db.execute('INSERT OR REPLACE INTO telegram_discoveries VALUES (?, ?, ?, ?)',
                           (*key, message_id, json.dumps(item, ensure_ascii=False)))
            db.execute("""UPDATE telegram_scan_cursors SET next_before=?, remaining=?
                       WHERE account=? AND source=?""", (message_id, remaining, *key))

    def finish(self, key):
        with self._store.lock, self._store.db as db:
            db.execute("""UPDATE telegram_scan_cursors SET completed=upper_id, upper_id=NULL,
                       next_before=0, remaining=NULL, start_at=NULL WHERE account=? AND source=?""", key)

    def items(self, key):
        with self._store.lock:
            rows = self._store.db.execute("""SELECT payload FROM telegram_discoveries
                WHERE account=? AND source=? ORDER BY message_id DESC""", key).fetchall()
        return [json.loads(row[0]) for row in rows]


class _ScopedDiscovery:
    """Use a different cursor when backfilling past the usual incremental baseline."""
    def __init__(self, journal, namespace):
        self.journal, self.namespace = journal, namespace

    def key(self, key):
        return key[0], f'{self.namespace}/{key[1]}'

    def cursor(self, key):
        return self.journal.cursor(self.key(key))

    def items(self, key):
        return self.journal.items(self.key(key))

    def begin(self, key, *args):
        return self.journal.begin(self.key(key), *args)

    def record(self, key, *args):
        return self.journal.record(self.key(key), *args)

    def finish(self, key):
        return self.journal.finish(self.key(key))
