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
