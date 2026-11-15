import tempfile
import unittest
from pathlib import Path

from local_video.discovery import Video, media_from_message
from local_video.store import Store


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path.cwd() / 'audit')
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'queue.db')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.store.close)

    def add(self, tid='1', mid='1', lib='a', state='pending', quality='best', **meta):
        job = self.store.enqueue(Video(tid, mid, 'alice', 'https://video.twimg.com/a.mp4', 'https://x.com/alice/status/' + tid, **meta), self.root / lib, quality)
        self.store.set_state(job, state)
        return job

    def test_tags_shared_across_libraries_and_scoped_partial_completion(self):
        self.add(state='completed'); self.add(mid='2'); self.add(lib='b')
        self.store.catalog.set_tags('1', ['#旅行', 'ＲｅＦ'])
        a = self.store.catalog.page(output=self.root / 'a', filter='completed')
        self.assertEqual(a['total'], 1)
        self.assertEqual(a['counts']['pending'], 1)
        self.assertEqual(len(a['tasks'][0]['media']), 2)
        self.assertEqual(set(a['tasks'][0]['tags']), {'旅行', 'ReF'})
        self.assertEqual(self.store.catalog.page(filter='completed')['total'], 0)
        self.assertEqual(self.store.catalog.page(output=self.root / 'b', filter='completed')['total'], 0)
        self.assertEqual(self.store.catalog.tags(self.root / 'b'), [{'name': 'ReF', 'count': 1}, {'name': '旅行', 'count': 1}])

    def test_query_tags_and_untagged_pagination_ignore(self):
        for tid in ('1', '2', '3'):
            self.add(tid=tid, tweet_text='beautiful beach')
        self.store.catalog.set_tags('1', ['Travel', '参考'])
        self.store.catalog.set_tags('2', ['travel'])
        page = self.store.catalog.page(query='beach #ＴＲＡＶＥＬ #参考')
        self.assertEqual([t['id'] for t in page['tasks']], ['1'])
        self.assertEqual(self.store.catalog.page(untagged=True)['tasks'][0]['id'], '3')
        page = self.store.catalog.page(limit=1, offset=1)
        self.assertEqual((page['total'], page['offset'], page['tasks'][0]['id']), (3, 1, '2'))
        self.store.ignore_tweets(['2'])
        self.assertEqual(self.store.catalog.page()['total'], 2)
        self.assertEqual(self.store.catalog.page(filter='ignored')['total'], 1)

    def test_rename_collision_delete_and_validation_are_atomic(self):
        self.add(); self.add(tid='2')
        self.store.catalog.set_tags('1', ['Foo', 'bar'])
        self.store.catalog.set_tags('2', ['foo'])
        self.store.catalog.rename_tag('#FOO', 'BAR')
        self.assertEqual(self.store.catalog.tags(), [{'name': 'bar', 'count': 2}])
        for invalid in ([''], ['a b'], ['💥'], ['a' * 33], [str(i) for i in range(21)]):
            with self.assertRaises(ValueError): self.store.catalog.set_tags('1', invalid)
        self.assertEqual(self.store.catalog.page(query='#bar')['total'], 2)
        self.store.catalog.delete_tag('ＢＡＲ')
        self.assertEqual(self.store.catalog.page(untagged=True)['total'], 2)

    def test_author_metadata_enrichment_and_public_whitelist(self):
        media = media_from_message((3, 'https://video.twimg.com/a.mp4', {'type': 'video', 'tweet_id': '1', 'author': {'id': 42, 'name': 'alice', 'nick': 'Alice 姓', 'profile_image': 'https://pbs.twimg.com/profile_images/42/a.jpg'}}))
        job = self.store.enqueue(media, self.root / 'a', 'best')
        task = self.store.catalog.page()['tasks'][0]
        self.assertEqual(task['display_name'], 'Alice 姓')
        self.assertTrue(task['avatar_available'])
        self.assertNotIn('avatar_url', repr(task))
        self.assertNotIn('https://', repr(task))
        self.assertEqual(self.store.catalog.author('1')['author_id'], '42')
        self.store.update_metadata(job, Video('1', '1', 'alice', '', '', display_name='New name'))
        self.assertEqual(self.store.catalog.page()['tasks'][0]['display_name'], 'New name')
        self.assertTrue(self.store.catalog.author('1')['avatar_url'])

    def test_library_counts_unique_media_and_backfill_restart(self):
        self.add(state='completed'); self.add(state='completed', quality='720p')
        self.add(mid='2'); self.add(tid='2', lib='b', state='completed')
        self.assertEqual(self.store.catalog.library_counts(self.root / 'a'), {'message_count': 1, 'media_count': 1})
        self.store.catalog.set_tags('1', ['save'])
        with self.store.lock, self.store.db:
            self.store.db.execute('DELETE FROM catalog_messages')
        from local_video.catalog import Catalog
        self.store.catalog = Catalog(self.store.db, self.store.lock)
        self.assertEqual(self.store.catalog.page()['total'], 2)
        self.assertEqual(self.store.catalog.page(query='#save')['total'], 1)
        self.assertEqual(len(self.store.list()), 4)

    def test_scoped_search_never_matches_other_library_sibling(self):
        self.add(tweet_text='local words')
        self.add(lib='b', tweet_text='secret elsewhere', state='completed')
        self.assertEqual(self.store.catalog.page(query='secret', output=self.root / 'a')['total'], 0)
        self.assertEqual(self.store.catalog.page(output=self.root / 'a', filter='completed')['total'], 0)
        self.assertEqual(self.store.catalog.library_counts(self.root / 'a'), {'message_count': 0, 'media_count': 0})

    def test_tag_transaction_rollback_and_bounded_filter_input(self):
        self.add()
        self.store.catalog.set_tags('1', ['original'])
        with self.store.db:
            self.store.db.execute("CREATE TRIGGER fail_tag BEFORE INSERT ON catalog_message_tags WHEN NEW.tag='bad' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.catalog.set_tags('1', ['new', 'bad'])
        self.assertEqual(self.store.catalog.page()['tasks'][0]['tags'], ['original'])
        with self.assertRaises(ValueError):
            self.store.catalog.page(tags=[str(i) for i in range(21)])

    def test_backfill_more_than_batch_preserves_jobs_and_restart_tags(self):
        # Simulate legacy jobs arriving without the new projection hooks.
        self.add()
        with self.store.db:
            self.store.db.executemany('''INSERT INTO jobs(tweet_id,media_id,author,url,source,output,quality,state,local_path)
                VALUES(?,'1','legacy','https://video.twimg.com/a.mp4','fixture',?,'best','completed','preserve-path')''',
                [(str(i), str(self.root / 'old')) for i in range(100, 1101)])
        from local_video.catalog import Catalog
        self.store.catalog = Catalog(self.store.db, self.store.lock)
        self.store.catalog.set_tags('100', ['Persist'])
        self.store.catalog = Catalog(self.store.db, self.store.lock)
        self.assertEqual(self.store.catalog.page()['total'], 1002)
        self.assertEqual(self.store.catalog.page(query='#persist')['tasks'][0]['id'], '100')
        self.assertEqual(self.store.catalog.library_counts(self.root / 'old'), {'message_count': 1001, 'media_count': 1001})
        self.assertEqual(self.store.tweet_rows('100')[0]['local_path'], 'preserve-path')

    def test_untrusted_avatar_is_discarded_without_changing_media_path(self):
        media = media_from_message((3, 'https://video.twimg.com/a.mp4', {'type': 'video', 'tweet_id': '1', 'user': {'id': 4, 'name': 'alice', 'nick': 'Display', 'profile_image': 'https://evil.example/avatar.jpg'}}))
        self.assertEqual(media.avatar_url, '')
        self.assertEqual(media.relative_path, 'alice/1/1_1.mp4')

    def test_page_query_count_is_constant_for_one_and_fifty_messages(self):
        for i in range(50):
            self.add(tid=str(i + 1), author_id='42', display_name='Alice')
            self.store.catalog.set_tags(str(i + 1), ['test'])
        counts = []
        for limit in (1, 50):
            statements = []
            self.store.db.set_trace_callback(statements.append)
            try:
                result = self.store.catalog.page(limit=limit)
            finally:
                self.store.db.set_trace_callback(None)
            self.assertEqual(len(result['tasks']), limit)
            self.assertTrue(all(t['tags'] == ['test'] and t['display_name'] == 'Alice' for t in result['tasks']))
            counts.append(len(statements))
        self.assertEqual(counts[0], counts[1])
        self.assertLessEqual(counts[1], 5)

    def test_legacy_backup_precedes_schema_changes_and_is_not_replaced(self):
        from contextlib import closing
        import sqlite3
        path = self.root / 'legacy.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('''CREATE TABLE jobs (
                id INTEGER PRIMARY KEY,tweet_id TEXT,media_id TEXT,author TEXT,url TEXT,source TEXT,
                width INTEGER,height INTEGER,output TEXT,quality TEXT,state TEXT,error TEXT DEFAULT '',
                bytes INTEGER DEFAULT 0,sha256 TEXT DEFAULT '',updated REAL,
                UNIQUE(tweet_id,media_id,output,quality))''')
            db.execute("INSERT INTO jobs(tweet_id,media_id,author,url,source,output,quality,state) VALUES('1','1','a','url','source','old/path','best','completed')")
        store = Store(path)
        store.close()
        backup = path.with_name(path.name + '.pre-catalog-v1.bak')
        self.assertTrue(backup.is_file())
        before = backup.read_bytes()
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('SELECT output FROM jobs').fetchone()[0], 'old/path')
            self.assertNotIn('verification', [r[1] for r in db.execute('PRAGMA table_info(jobs)')])
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='catalog_migrations'").fetchone())
        store = Store(path)
        store.close()
        self.assertEqual(before, backup.read_bytes())
        self.assertFalse((self.root / 'queue.db.pre-catalog-v1.bak').exists())

    def test_deferred_catalog_only_backfills_when_ensured_once(self):
        self.add()
        with self.store.db:
            self.store.db.execute('DELETE FROM catalog_messages')
        path = self.root / 'queue.db'
        other = Store(path, defer_catalog=True)
        self.addCleanup(other.close)
        self.assertIsNone(other.db.execute('SELECT 1 FROM catalog_messages').fetchone())
        other.catalog.ensure_ready()
        self.assertEqual(other.catalog.page()['total'], 1)
        statements = []
        other.db.set_trace_callback(statements.append)
        try:
            other.catalog.ensure_ready()
        finally:
            other.db.set_trace_callback(None)
        self.assertEqual(statements, [])

    def test_deferred_migration_failure_retries_from_last_committed_batch(self):
        import sqlite3
        from local_video.catalog import Catalog
        self.add()
        with self.store.db:
            self.store.db.executemany('''INSERT INTO jobs(tweet_id,media_id,author,url,source,output,quality)
                VALUES(?,'1','legacy','url','source','untouched/path','best')''',
                [(str(i),) for i in range(100, 1101)])
            self.store.db.execute("CREATE TRIGGER fail_migration BEFORE INSERT ON catalog_messages WHEN NEW.message_id='700' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        catalog = Catalog(self.store.db, self.store.lock, defer=True)
        with self.assertRaises(sqlite3.IntegrityError):
            catalog.ensure_ready()
        self.assertEqual(self.store.db.execute("SELECT cursor FROM catalog_migrations WHERE name='jobs-v1'").fetchone()[0], 500)
        with self.store.db:
            self.store.db.execute('DROP TRIGGER fail_migration')
        catalog.ensure_ready()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM catalog_messages').fetchone()[0], 1002)
        self.assertEqual(catalog.library_counts('untouched/path'), {'message_count': 0, 'media_count': 0})

    def test_failed_backup_publishes_no_partial_file_and_closes_database(self):
        from contextlib import closing
        import sqlite3
        from unittest.mock import patch
        path = self.root / 'backup-failure.db'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE jobs(id INTEGER PRIMARY KEY)')
            db.execute('INSERT INTO jobs VALUES(1)')
        with patch('local_video.store.os.link', side_effect=OSError('fixture backup failure')):
            with self.assertRaisesRegex(OSError, 'fixture backup failure'):
                Store(path)
        self.assertFalse(list(self.root.glob(path.name + '.pre-catalog-v1.bak*')))
        with closing(sqlite3.connect(path)) as db:
            self.assertEqual([r[1] for r in db.execute('PRAGMA table_info(jobs)')], ['id'])
        # Windows will reject this move if a failed Store retained an open handle.
        path.rename(path.with_suffix('.closed'))
